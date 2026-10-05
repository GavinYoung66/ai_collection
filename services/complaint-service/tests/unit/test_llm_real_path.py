"""V2：USE_MOCK_LLM 装配路径断言。"""
from __future__ import annotations

import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.api.deps import reset_llm_provider
from app.config import settings
from app.llm import real_transport as real_transport_mod
from app.llm.provider import ComplaintLLMProvider
from app.llm.real_transport import RealLLMTransport, _is_placeholder
from common.llm import reliability as reliability_mod
from common.llm.reliability import (
    LLMRateLimitError,
    load_llm_rate_limit,
    set_default_service,
)
from common.resilience._redis import configure_redis


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_llm_provider()
    yield
    reset_llm_provider()


def test_mock_path_when_flag_true(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
    p = ComplaintLLMProvider()
    assert p.using_mock is True
    assert p._get_transport().__class__.__name__ == "MockLLMProvider"


def test_real_path_when_flag_false(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    p = ComplaintLLMProvider()
    assert p.using_mock is False
    assert isinstance(p._get_transport(), RealLLMTransport)


@pytest.mark.asyncio
async def test_real_transport_rejects_placeholder_key(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_DASHSCOPE_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_API_KEY", "change_me")
    t = RealLLMTransport()
    with pytest.raises(RuntimeError, match="real LLM key missing"):
        await t.invoke("请给出判责 JSON", agent_role="verdict")


@pytest.mark.asyncio
async def test_deepseek_falls_back_to_qwen_when_only_dashscope(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_DASHSCOPE_API_KEY", "sk-test-dashscope-key")
    monkeypatch.setattr(settings, "LLM_API_KEY", "change_me")
    t = RealLLMTransport()
    model, provider, api_key, _base = t._resolve_model_and_creds("verdict")
    assert model == "qwen-plus"
    assert provider == "dashscope"
    assert api_key.startswith("sk-test")


def test_placeholder_helper():
    assert _is_placeholder("change_me")
    assert not _is_placeholder("sk-live-not-real-but-nonempty")


# ---------------------------------------------------------------------------
# LLM 限流阈值：与 common.invoke_llm 共用单一事实源（LLM_RATE_LIMIT/WINDOW）
# ---------------------------------------------------------------------------
@pytest.fixture
def _shared_rate_config():
    """固定服务名；用例后还原服务名，并按已还原的环境变量重载阈值，避免污染他例。"""
    set_default_service("csmp")
    yield
    set_default_service(None)
    load_llm_rate_limit()


def _stub_post_chat(monkeypatch, payload='{"ok": 1}'):
    """替换真实 HTTP 出口，返回最小 chat/completions 响应。"""

    async def _fake_post(*, base_url, api_key, model, messages, timeout, **kwargs):
        return {"choices": [{"message": {"content": payload}}]}

    monkeypatch.setattr(real_transport_mod, "post_chat_completions", _fake_post)


async def _post_once(t: RealLLMTransport) -> dict:
    return await t._post_chat(
        prompt="请给出判责 JSON",
        system="sys",
        model="qwen-plus",
        provider="dashscope",
        api_key="sk-test",
        base_url="https://llm.invalid",
        agent_role="verdict",
        trace_id="t-1",
        session_id="",
    )


def test_real_transport_has_no_local_rate_limit_constants():
    # 单一事实源：transport 本地不得再保留 _LLM_RATE_LIMIT/_LLM_RATE_WINDOW 常量
    assert not hasattr(real_transport_mod, "_LLM_RATE_LIMIT")
    assert not hasattr(real_transport_mod, "_LLM_RATE_WINDOW")
    # 两处入口拿到的是同一个函数对象（改一处来源，两处一致）
    assert real_transport_mod.get_llm_rate_limit is reliability_mod.get_llm_rate_limit


@pytest.mark.asyncio
async def test_real_transport_rate_limit_reads_shared_env_source(
    monkeypatch, _shared_rate_config
):
    # spec Scenario：设 LLM_RATE_LIMIT=600，公共层与 transport 读到同一阈值并按新值计数
    monkeypatch.setenv("LLM_RATE_LIMIT", "600")
    monkeypatch.setenv("LLM_RATE_WINDOW", "30")
    load_llm_rate_limit()
    assert reliability_mod.get_llm_rate_limit() == (600, 30)

    captured = {}
    real_allow = real_transport_mod.rate_limit_allow

    async def _spy_allow(key, limit, window=60, *, context=None):
        captured.update(key=key, limit=limit, window=window)
        return await real_allow(key, limit=limit, window=window, context=context)

    monkeypatch.setattr(real_transport_mod, "rate_limit_allow", _spy_allow)
    _stub_post_chat(monkeypatch)

    result = await _post_once(RealLLMTransport())

    assert result == {"ok": 1}
    # 限流 key 模板一字未改：{svc}:llm_ratelimit:{model}
    assert captured == {
        "key": "csmp:llm_ratelimit:qwen-plus",
        "limit": 600,
        "window": 30,
    }


@pytest.mark.asyncio
async def test_real_transport_over_limit_raises_typed_error(
    monkeypatch, _shared_rate_config
):
    # 阈值=1：首次放行计数=1；第二次计数=2 超限，抛 LLMRateLimitError（异常类型不变）
    monkeypatch.setenv("LLM_RATE_LIMIT", "1")
    monkeypatch.setenv("LLM_RATE_WINDOW", "60")
    load_llm_rate_limit()
    _stub_post_chat(monkeypatch)

    t = RealLLMTransport()
    assert await _post_once(t) == {"ok": 1}
    with pytest.raises(LLMRateLimitError, match=r"csmp:llm_ratelimit:qwen-plus"):
        await _post_once(t)


@pytest.mark.asyncio
async def test_real_transport_redis_down_silent_allow(
    monkeypatch, _shared_rate_config
):
    # spec Scenario：Redis 不可达时静默放行（fail-open 方向不变），调用正常成功
    class _RedisDown:
        async def set(self, *args, **kwargs):
            raise RedisConnectionError("redis down")

        async def incr(self, *args, **kwargs):
            raise RedisConnectionError("redis down")

    configure_redis(_RedisDown())
    _stub_post_chat(monkeypatch)

    assert await _post_once(RealLLMTransport()) == {"ok": 1}


class _CountingRedis:
    """最小计数 Redis：记录每个限流 key 的 SET NX / INCR 次数（D-1 双计数取证用）。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]


@pytest.mark.asyncio
async def test_real_provider_nested_chain_counts_rate_limit_once(
    monkeypatch, _shared_rate_config
):
    # D-1 回归：生产真路径 ComplaintLLMProvider → invoke_llm → RealLLMTransport 嵌套，
    # 同一请求对 csmp:llm_ratelimit:qwen-plus 只能 INCR 1 次（编排层计、transport 跳过）；
    # 连续 2 次请求必须线性计 2 次，旧实现会双 INCR 计成 4 次。
    fake = _CountingRedis()
    configure_redis(fake)

    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_DASHSCOPE_API_KEY", "sk-test")
    monkeypatch.setattr(settings, "LLM_API_KEY", "change_me")
    monkeypatch.delenv("LLM_RATE_LIMIT", raising=False)
    monkeypatch.delenv("LLM_RATE_WINDOW", raising=False)
    load_llm_rate_limit()
    _stub_post_chat(monkeypatch, payload='{"verdict": "ok"}')

    provider = ComplaintLLMProvider()
    await provider.invoke("请给出判责 JSON", agent_role="verdict")
    assert fake.counts["csmp:llm_ratelimit:qwen-plus"] == 1

    await provider.invoke("请再次给出判责 JSON", agent_role="verdict")
    assert fake.counts["csmp:llm_ratelimit:qwen-plus"] == 2

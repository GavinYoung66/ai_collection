"""LLM 用量埋点单测（B3：4.2~4.4）。

覆盖：
- 4.2 token usage ContextVar 跨调用隔离（第二次 missing / 异常不残留 / fallback 候选不串）；
- 4.3 三埋点 structlog ``event=llm_call`` 五种 status 各一条且字段齐全；
- 4.4 六指标打点：ok/timeout/error 计入 calls+latency+tokens；fallback/rate_limited/breaker_open
  由独立计数器承载，rate_limited/breaker_open **不计入** llm_calls_total。
"""
from __future__ import annotations

import asyncio

import pytest
from prometheus_client import REGISTRY
from structlog.testing import capture_logs

from common.llm import reliability as reliability_mod
from common.llm import transport as transport_mod
from common.llm.reliability import (
    LLMRateLimitError,
    invoke_llm,
    reset_llm_transport,
    set_default_service,
    set_llm_transport,
)
from common.resilience._redis import configure_redis, reset_redis
from common.resilience.timeout import Timeout

SVC = "csmp"

_LOG_FIELDS = {
    "svc",
    "model",
    "fallback_from",
    "breaker_key",
    "status",
    "latency_ms",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "token_source",
}


class FakeRedis:
    """最小计数 Redis：限流放行即可（并发无关）。"""

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

    async def expire(self, key, window):
        return None

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)


@pytest.fixture(autouse=True)
def _isolate():
    configure_redis(FakeRedis())
    set_default_service(SVC)
    reliability_mod._llm_breakers.clear()
    reset_llm_transport()
    transport_mod._llm_last_usage.set(None)
    yield
    set_default_service(None)
    reset_redis()
    reset_llm_transport()
    reliability_mod._llm_breakers.clear()
    transport_mod._llm_last_usage.set(None)


def _val(name: str, labels: dict) -> float:
    v = REGISTRY.get_sample_value(name, labels)
    return 0.0 if v is None else v


def _llm_calls(cap) -> list[dict]:
    return [e for e in cap if e.get("event") == "llm_call"]


def _usage_body(content: str, *, prompt: int, completion: int) -> dict:
    return {
        "choices": [{"message": {"content": content}}],
        "usage": {
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": prompt + completion,
        },
    }


# ---------------------------------------------------------------------------
# 4.2 ContextVar 跨调用隔离
# ---------------------------------------------------------------------------
async def test_contextvar_isolation_second_call_missing(monkeypatch):
    """① 连续两次调用：第一次有 usage、第二次无 usage → 第二次 token_source=missing。

    走真实 post_chat_completions（仅 mock 出口 post_json），端到端验证
    usage 能穿过 asyncio.wait_for 边界被本次调用读到。
    """
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    responses = [
        _usage_body("one", prompt=5, completion=3),
        {"choices": [{"message": {"content": "two"}}]},  # 第二次无 usage
    ]
    state = {"i": 0}

    async def _fake_post_json(**kwargs):
        body = responses[state["i"]]
        state["i"] += 1
        return body

    monkeypatch.setattr(transport_mod, "post_json", _fake_post_json)

    async def transport(model, messages, timeout_seconds):
        body = await transport_mod.post_chat_completions(
            base_url="https://llm.invalid",
            api_key="k",
            model=model,
            messages=messages,
            timeout=timeout_seconds,
        )
        return body["choices"][0]["message"]["content"]

    set_llm_transport(transport)

    with capture_logs() as cap:
        r1 = await invoke_llm("m1", [])
        r2 = await invoke_llm("m1", [])

    assert r1["content"] == "one"
    assert r2["content"] == "two"
    entries = _llm_calls(cap)
    assert [e["token_source"] for e in entries] == ["usage", "missing"]
    assert entries[0]["prompt_tokens"] == 5
    assert entries[0]["completion_tokens"] == 3
    assert entries[0]["total_tokens"] == 8
    assert entries[1]["prompt_tokens"] == 0
    assert entries[1]["completion_tokens"] == 0


async def test_contextvar_cleared_after_transport_exception(monkeypatch):
    """② 第一次调用 transport 抛异常（写后失败）→ 第二次调用不残留。"""
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport_fail(model, messages, timeout_seconds):
        # 先写 usage 再抛异常：验证异常路径不把它残留给下一次调用
        transport_mod._llm_last_usage.set(
            {"prompt_tokens": 9, "completion_tokens": 1, "total_tokens": 10}
        )
        raise RuntimeError("boom")

    set_llm_transport(transport_fail)
    with capture_logs():
        result = await invoke_llm("m1", [])
    assert result["degraded"] is True
    for status in ("error",):
        assert (
            _val("llm_calls_total", {"svc": SVC, "model": "m1", "status": status}) >= 1
        )
    # 外层 ContextVar 不残留（finally 兜底清零）
    assert transport_mod._llm_last_usage.get() is None

    async def transport_ok(model, messages, timeout_seconds):
        return "ok"

    set_llm_transport(transport_ok)
    with capture_logs() as cap2:
        await invoke_llm("m1", [])
    entries = _llm_calls(cap2)
    assert len(entries) == 1
    assert entries[0]["token_source"] == "missing"


async def test_fallback_candidates_usage_isolated(monkeypatch):
    """③ fallback 两候选各自 usage 独立不串。"""
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport(model, messages, timeout_seconds):
        if model == "iso-primary":
            # primary 写自己的 usage 后失败 → 不得串到 backup
            transport_mod._llm_last_usage.set(
                {"prompt_tokens": 111, "completion_tokens": 0, "total_tokens": 111}
            )
            raise RuntimeError("primary down")
        transport_mod._llm_last_usage.set(
            {"prompt_tokens": 222, "completion_tokens": 7, "total_tokens": 229}
        )
        return "ok"

    set_llm_transport(transport)

    with capture_logs() as cap:
        result = await invoke_llm("iso-primary", [], fallback_chain=["iso-backup"])

    assert result["model"] == "iso-backup"
    entries = _llm_calls(cap)
    ok_entries = [e for e in entries if e["status"] == "ok"]
    err_entries = [e for e in entries if e["status"] == "error"]
    assert len(err_entries) == 1
    assert err_entries[0]["model"] == "iso-primary"
    assert err_entries[0]["token_source"] == "missing"  # error 终态不读 usage
    assert len(ok_entries) == 1
    assert ok_entries[0]["model"] == "iso-backup"
    assert ok_entries[0]["fallback_from"] == "iso-primary"
    assert ok_entries[0]["prompt_tokens"] == 222  # 未被 primary 的 111 串扰
    assert ok_entries[0]["completion_tokens"] == 7


# ---------------------------------------------------------------------------
# 4.3 三埋点：五种 status 日志各一条且字段齐全
# ---------------------------------------------------------------------------
async def test_all_five_status_logs_have_complete_fields(monkeypatch):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport_ok(model, messages, timeout_seconds):
        transport_mod._llm_last_usage.set(
            {"prompt_tokens": 4, "completion_tokens": 2, "total_tokens": 6}
        )
        return "ok"

    # ① ok
    set_llm_transport(transport_ok)
    with capture_logs() as cap_ok:
        await invoke_llm("m-ok", [])

    # ② timeout
    async def transport_timeout(model, messages, timeout_seconds):
        await asyncio.sleep(0.05)
        return "late"

    set_llm_transport(transport_timeout)
    with capture_logs() as cap_to:
        await invoke_llm("m-timeout", [], timeout=Timeout(total=0.01))

    # ③ error
    async def transport_err(model, messages, timeout_seconds):
        raise RuntimeError("boom")

    set_llm_transport(transport_err)
    with capture_logs() as cap_err:
        await invoke_llm("m-err", [])

    # ④ breaker_open（预置打开该模型 breaker）
    breaker = reliability_mod._llm_breakers.get_or_create(
        f"{SVC}:llm_degraded:m-open"
    )
    for _ in range(3):
        await breaker.record_failure()
    set_llm_transport(transport_ok)
    with capture_logs() as cap_bo:
        await invoke_llm("m-open", [])

    # ⑤ rate_limited
    monkeypatch.setattr(reliability_mod, "_LLM_RATE_LIMIT", 0)
    set_llm_transport(transport_ok)
    with capture_logs() as cap_rl, pytest.raises(LLMRateLimitError):
        await invoke_llm("m-rl", [])

    logs = {
        "ok": _llm_calls(cap_ok),
        "timeout": _llm_calls(cap_to),
        "error": _llm_calls(cap_err),
        "breaker_open": _llm_calls(cap_bo),
        "rate_limited": _llm_calls(cap_rl),
    }
    for status, entries in logs.items():
        assert len(entries) == 1, f"{status}: {entries}"
        entry = entries[0]
        assert entry["status"] == status
        assert entry["event"] == "llm_call"
        assert set(entry) >= _LOG_FIELDS
        assert entry["svc"] == SVC
        assert entry["token_source"] in ("usage", "missing")

    # 拒绝态：token/latency 全 0、missing
    for status in ("breaker_open", "rate_limited"):
        entry = logs[status][0]
        assert entry["latency_ms"] == 0
        assert entry["token_source"] == "missing"
        assert entry["prompt_tokens"] == 0
        assert entry["completion_tokens"] == 0
        assert entry["total_tokens"] == 0

    # ok：usage 真实值；timeout/error：missing 且 0
    assert logs["ok"][0]["token_source"] == "usage"
    assert logs["ok"][0]["prompt_tokens"] == 4
    assert logs["ok"][0]["total_tokens"] == 6
    for status in ("timeout", "error"):
        entry = logs[status][0]
        assert entry["token_source"] == "missing"
        assert entry["total_tokens"] == 0


# ---------------------------------------------------------------------------
# 4.4 六指标打点
# ---------------------------------------------------------------------------
async def test_ok_call_increments_calls_latency_tokens(monkeypatch):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport(model, messages, timeout_seconds):
        transport_mod._llm_last_usage.set(
            {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        )
        return "ok"

    set_llm_transport(transport)
    calls = {"svc": SVC, "model": "metric-ok", "status": "ok"}
    lat = {"svc": SVC, "model": "metric-ok"}
    tok_p = {"svc": SVC, "model": "metric-ok", "kind": "prompt"}
    tok_c = {"svc": SVC, "model": "metric-ok", "kind": "completion"}

    base_calls = _val("llm_calls_total", calls)
    base_lat = _val("llm_call_latency_seconds_count", lat)
    base_p = _val("llm_tokens_total", tok_p)
    base_c = _val("llm_tokens_total", tok_c)

    await invoke_llm("metric-ok", [])

    assert _val("llm_calls_total", calls) == base_calls + 1
    assert _val("llm_call_latency_seconds_count", lat) == base_lat + 1
    assert _val("llm_tokens_total", tok_p) == base_p + 10
    assert _val("llm_tokens_total", tok_c) == base_c + 5


async def test_fallback_counter(monkeypatch):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport(model, messages, timeout_seconds):
        if model == "fb-primary":
            raise RuntimeError("down")
        return "ok"

    set_llm_transport(transport)
    fb = {"svc": SVC, "from_model": "fb-primary", "to_model": "fb-backup"}
    p_err = {"svc": SVC, "model": "fb-primary", "status": "error"}
    b_ok = {"svc": SVC, "model": "fb-backup", "status": "ok"}

    base_fb = _val("llm_fallback_total", fb)
    base_p = _val("llm_calls_total", p_err)
    base_b = _val("llm_calls_total", b_ok)

    await invoke_llm("fb-primary", [], fallback_chain=["fb-backup"])

    assert _val("llm_fallback_total", fb) == base_fb + 1
    # 两候选各产生一条 calls：primary=error、backup=ok
    assert _val("llm_calls_total", p_err) == base_p + 1
    assert _val("llm_calls_total", b_ok) == base_b + 1


async def test_rate_limited_counter_excludes_calls(monkeypatch):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    monkeypatch.setattr(reliability_mod, "_LLM_RATE_LIMIT", 0)

    async def transport(model, messages, timeout_seconds):
        return "ok"

    set_llm_transport(transport)
    rl = {"svc": SVC, "model": "metric-rl"}
    base_rl = _val("llm_rate_limited_total", rl)

    with pytest.raises(LLMRateLimitError):
        await invoke_llm("metric-rl", [])

    assert _val("llm_rate_limited_total", rl) == base_rl + 1
    # 未发起 transport：该模型无任何 llm_calls_total 序列
    for status in ("ok", "timeout", "error"):
        assert _val("llm_calls_total", {"svc": SVC, "model": "metric-rl", "status": status}) == 0.0


async def test_breaker_open_counter_excludes_calls(monkeypatch):
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)

    async def transport(model, messages, timeout_seconds):
        return "ok"

    breaker = reliability_mod._llm_breakers.get_or_create(
        f"{SVC}:llm_degraded:metric-open"
    )
    for _ in range(3):
        await breaker.record_failure()

    set_llm_transport(transport)
    bo = {"svc": SVC, "model": "metric-open"}
    base_bo = _val("llm_breaker_open_total", bo)

    result = await invoke_llm("metric-open", [])

    assert result["degraded"] is True
    assert _val("llm_breaker_open_total", bo) == base_bo + 1
    for status in ("ok", "timeout", "error"):
        assert (
            _val("llm_calls_total", {"svc": SVC, "model": "metric-open", "status": status})
            == 0.0
        )


# ---------------------------------------------------------------------------
# 审核修复回归：D2 残留清零 / D3 脏值埋点不影响主链
# ---------------------------------------------------------------------------
async def test_retry_success_does_not_inherit_residual_usage(monkeypatch):
    """审核 D2：写 usage 后失败的调用，残留不得串到重试/下一候选的成功调用。

    模拟"写后失败"路径（如 post_chat_completions 写入 usage 后 transport 侧
    校验抛错）：第一次 transport 先写 ContextVar 再抛错；第二次直接成功且
    不写 usage。修复前第二次会读到旧 usage（token 串扰），修复后 missing。
    """
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    calls: list[str] = []

    async def write_then_fail_then_silent(model, messages, timeout_seconds):
        calls.append(model)
        if len(calls) == 1:
            # 与 post_chat_completions 成功路径等价：写入 usage 后调用失败
            transport_mod._llm_last_usage.set({"prompt_tokens": 7, "completion_tokens": 3})
            raise RuntimeError("post-write failure")
        return "ok"  # 第二次成功但不写 usage

    set_llm_transport(write_then_fail_then_silent)
    with capture_logs() as cap:
        result = await invoke_llm("retry-residual", [])

    assert result["content"] == "ok"
    ok_logs = [e for e in _llm_calls(cap) if e["status"] == "ok"]
    assert len(ok_logs) == 1
    # 第二次成功调用不得继承第一次失败残留的 usage
    assert ok_logs[0]["token_source"] == "missing"
    assert ok_logs[0]["prompt_tokens"] == 0 and ok_logs[0]["completion_tokens"] == 0


async def test_dirty_usage_tokens_does_not_break_call():
    """审核 D3：usage 含非数值脏值时埋点不抛异常，成功调用不受影响。

    埋点解析失败按 0 记（token_source 仍为 usage），绝不把埋点异常冒泡成
    transport 失败（否则成功调用会被改判 error 并触发 fallback）。
    """
    dirty_usage = {"prompt_tokens": "abc", "completion_tokens": 2}

    async def transport_write_dirty_usage(model, messages, timeout_seconds):
        # 模拟上游返回脏 usage 并已由 post_chat_completions 写入 ContextVar
        transport_mod._llm_last_usage.set(dirty_usage)
        return "fine"

    set_llm_transport(transport_write_dirty_usage)
    with capture_logs() as cap:
        result = await invoke_llm("dirty-usage", [])

    assert result["content"] == "fine"  # 主调用链正常返回
    ok_logs = [e for e in _llm_calls(cap) if e["status"] == "ok"]
    assert len(ok_logs) == 1
    assert ok_logs[0]["token_source"] == "usage"
    assert ok_logs[0]["prompt_tokens"] == 0
    assert ok_logs[0]["completion_tokens"] == 0
    assert ok_logs[0]["total_tokens"] == 0
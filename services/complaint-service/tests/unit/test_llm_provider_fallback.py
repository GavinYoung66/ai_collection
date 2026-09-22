"""LLM Provider fallback_chain 单元测试（spec: llm-layer）。

覆盖：
- 主模型不可用 → 自动走备用成功
- 整链失败 → LLMCallError（degraded outcome 转异常）
- primary 熔断 open 不阻塞 backup
- mock LLM 超时（timeout=0.1s）→ 整链超时 → LLMCallError
- fallback_chain 配置顺序校验（重复项 fail-fast / 空链 fail-fast）
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

from app.config import settings
from app.llm.provider import (
    ComplaintLLMProvider,
    LLMCallError,
    get_llm_fallback_chain,
)
from common.llm import reliability as llm_reliability
from common.resilience.timeout import Timeout


class LocalSchema(BaseModel):
    verdict: str
    confidence: float


_PRIMARY = "primary-test-model"
_BACKUP = "backup-test-model"


@pytest.fixture(autouse=True)
def _chain_env(monkeypatch):
    """固定两模型链 + 缩短重试退避 + 隔离全局熔断注册表。"""
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", f"{_PRIMARY},{_BACKUP}")
    monkeypatch.setattr(llm_reliability, "_LLM_RETRY_DELAY", 0.01)
    llm_reliability._llm_breakers.clear()
    yield
    llm_reliability._llm_breakers.clear()


class FlakyTransport:
    """primary 模型必失败、backup 模型成功的假 transport，记录被调模型序列。"""

    def __init__(self, fail_models: set[str] | None = None, delay: float = 0.0):
        self.models_called: list[str] = []
        self._fail_models = fail_models if fail_models is not None else {_PRIMARY}
        self._delay = delay

    async def invoke(self, prompt, schema=None, *, model=None, **_):
        self.models_called.append(model)
        if model in self._fail_models:
            raise RuntimeError(f"model {model} unavailable")
        if self._delay:
            await asyncio.sleep(self._delay)
        return {"verdict": "pending", "confidence": 0.5}


@pytest.mark.asyncio
async def test_primary_unavailable_falls_back_to_backup():
    """主模型不可用 → 自动走备用成功，不把主模型失败冒泡。"""
    t = FlakyTransport()
    p = ComplaintLLMProvider(transport=t)
    result = await p.invoke("请给出判责结论", LocalSchema, trace_id="t1")
    assert isinstance(result, LocalSchema)
    assert _PRIMARY in t.models_called  # 主模型先被尝试
    assert t.models_called[-1] == _BACKUP  # 最终由备用模型产出结果


@pytest.mark.asyncio
async def test_whole_chain_exhausted_raises_llm_call_error():
    """整条 fallback_chain 耗尽 → LLMCallError（degrade flag 由 invoke_llm 置位）。"""
    t = FlakyTransport(fail_models={_PRIMARY, _BACKUP})
    p = ComplaintLLMProvider(transport=t)
    with pytest.raises(LLMCallError):
        await p.invoke("请给出判责结论", LocalSchema, trace_id="t2")
    assert set(t.models_called) == {_PRIMARY, _BACKUP}  # 链上每个模型都被尝试
    # 审计日志记录整链降级错误
    assert p.call_log and p.call_log[0]["status"] == "error"
    assert "llm_chain_degraded" in p.call_log[0]["error"]


@pytest.mark.asyncio
async def test_primary_breaker_open_does_not_block_backup():
    """primary 熔断 open → 跳过主模型直接走 backup（per-model key 隔离）。"""
    breaker = llm_reliability._llm_breakers.get_or_create(
        llm_reliability.make_llm_breaker_key("csmp", _PRIMARY)
    )
    for _ in range(10):
        if breaker.state == "open":
            break
        await breaker.record_failure()
    assert breaker.state == "open"

    t = FlakyTransport()  # backup 本身健康
    p = ComplaintLLMProvider(transport=t)
    result = await p.invoke("请给出判责结论", LocalSchema, trace_id="t3")
    assert isinstance(result, LocalSchema)
    assert t.models_called == [_BACKUP]


@pytest.mark.asyncio
async def test_mock_llm_timeout_0_1s_chain_degraded():
    """mock LLM 延迟 0.2s > timeout total=0.1s → 超时不重试、换模型仍超时 → LLMCallError。"""
    t = FlakyTransport(fail_models=set(), delay=0.2)
    p = ComplaintLLMProvider(transport=t, llm_timeout=Timeout(total=0.1))
    with pytest.raises(LLMCallError):
        await p.invoke("请给出判责结论", LocalSchema, trace_id="t4")
    # 超时不重试：每个模型恰好 1 次
    assert t.models_called == [_PRIMARY, _BACKUP]


# ── 配置顺序合法性校验 ──


def test_chain_config_duplicate_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", "qwen-plus,qwen-plus")
    with pytest.raises(RuntimeError, match="重复"):
        get_llm_fallback_chain()


def test_chain_config_empty_fail_fast(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", "")
    monkeypatch.setattr(settings, "LLM_MODEL_PRIMARY", "")
    monkeypatch.setattr(settings, "LLM_MODEL_FALLBACK_1", "")
    monkeypatch.setattr(settings, "LLM_MODEL_FALLBACK_2", "")
    with pytest.raises(RuntimeError, match="为空"):
        get_llm_fallback_chain()


def test_chain_config_derived_from_primary_fallbacks(monkeypatch):
    """LLM_FALLBACK_CHAIN 留空 → 由 PRIMARY/FALLBACK_1/2 推导（向后兼容）。"""
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", "")
    monkeypatch.setattr(settings, "LLM_MODEL_PRIMARY", "deepseek-r1")
    monkeypatch.setattr(settings, "LLM_MODEL_FALLBACK_1", "qwen-plus")
    monkeypatch.setattr(settings, "LLM_MODEL_FALLBACK_2", "qwen-turbo")
    assert get_llm_fallback_chain() == ["deepseek-r1", "qwen-plus", "qwen-turbo"]


def test_chain_config_explicit_wins(monkeypatch):
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", " qwen-plus , qwen-turbo , deepseek-r1 ")
    assert get_llm_fallback_chain() == ["qwen-plus", "qwen-turbo", "deepseek-r1"]

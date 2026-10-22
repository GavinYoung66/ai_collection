"""LLM Provider 单元测试：Mock 路径返回本地 Schema + Token 累计。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from app.llm.provider import (
    _SCHEMA_RETRY_MAX,
    ComplaintLLMProvider,
    TokenLimitExceededError,
)


class LocalSchema(BaseModel):
    verdict: str
    confidence: float


@pytest.fixture
def provider():
    return ComplaintLLMProvider(max_token_threshold=100000)


@pytest.mark.asyncio
async def test_invoke_returns_schema_instance(provider):
    result = await provider.invoke("请给出判责结论", LocalSchema, trace_id="t1", session_id="s1")
    assert isinstance(result, LocalSchema)


@pytest.mark.asyncio
async def test_token_accumulated(provider):
    before = provider.total_tokens_consumed
    await provider.invoke("请给出判责结论", LocalSchema, trace_id="t1", session_id="s1")
    assert provider.total_tokens_consumed > before


@pytest.mark.asyncio
async def test_token_limit_exceeded():
    p = ComplaintLLMProvider(max_token_threshold=1)
    with pytest.raises(TokenLimitExceededError):
        await p.invoke("判责" * 100, LocalSchema, trace_id="t1", session_id="s1")


@pytest.mark.asyncio
async def test_audit_log_recorded(provider):
    await provider.invoke("请给出判责结论", LocalSchema, trace_id="t1", session_id="s1")
    assert len(provider.call_log) == 1
    assert provider.call_log[0]["status"] == "success"


class _BadSchemaTransport:
    """固定返回不符合 Schema 的载荷，用于覆盖校验失败路径。"""

    def __init__(self, payload: dict):
        self._payload = payload
        self.call_count = 0

    async def invoke(self, **_kwargs):
        # provider 以关键字传参（prompt=/schema=/trace_id=...），此处无需消费
        self.call_count += 1
        return self._payload


@pytest.mark.asyncio
async def test_schema_validation_failure_retries_max_times():
    """LLM 输出结构非法 → 有界重试 _SCHEMA_RETRY_MAX 次 → 抛 ValidationError。"""
    # 缺 verdict 且 confidence 类型错误
    transport = _BadSchemaTransport({"confidence": "not-a-number"})
    p = ComplaintLLMProvider(transport=transport, max_token_threshold=100000)

    with pytest.raises(ValidationError):
        await p.invoke("请给出判责结论", LocalSchema, trace_id="t1", session_id="s1")

    assert transport.call_count == _SCHEMA_RETRY_MAX


@pytest.mark.asyncio
async def test_schema_validation_failure_records_audit():
    """每次校验失败都落 schema_validation_failed 审计，且带 trace_id 可追溯。"""
    transport = _BadSchemaTransport({"confidence": "not-a-number"})
    p = ComplaintLLMProvider(transport=transport, max_token_threshold=100000)

    with pytest.raises(ValidationError):
        await p.invoke("请给出判责结论", LocalSchema, trace_id="t1", session_id="s1")

    failed = [entry for entry in p.call_log if entry["status"] == "error"]
    assert len(failed) == _SCHEMA_RETRY_MAX
    assert all("schema_validation_failed" in (entry["error"] or "") for entry in failed)
    assert all(entry["trace_id"] == "t1" for entry in failed)


class _RecordingBadTransport:
    """记录每次收到的 prompt，且始终返回非法结构。"""

    def __init__(self, payload: dict):
        self._payload = payload
        self.prompts: list[str] = []

    async def invoke(self, **kwargs):
        self.prompts.append(str(kwargs.get("prompt", "")))
        return self._payload


@pytest.mark.asyncio
async def test_schema_failure_feedback_injected_into_retry_prompt():
    """校验失败回灌：首次用原 prompt，重试时追加错误定位与修正指令。"""
    transport = _RecordingBadTransport({"confidence": "not-a-number"})
    p = ComplaintLLMProvider(transport=transport, max_token_threshold=100000)

    with pytest.raises(ValidationError):
        await p.invoke("请给出判责结论", LocalSchema, trace_id="t1", session_id="s1")

    assert len(transport.prompts) == _SCHEMA_RETRY_MAX
    # 首次保持原始 prompt 不变（不污染正常路径）
    assert transport.prompts[0] == "请给出判责结论"
    # 第 2 次已带错误反馈：含修正指令与缺失字段定位
    assert "请严格按 Schema 修正" in transport.prompts[1]
    assert "verdict" in transport.prompts[1]
    assert transport.prompts[1].startswith("请给出判责结论")

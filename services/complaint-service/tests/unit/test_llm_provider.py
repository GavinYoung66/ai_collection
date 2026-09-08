"""LLM Provider 单元测试：Mock 路径返回本地 Schema + Token 累计。"""
from __future__ import annotations

import pytest
from pydantic import BaseModel

from app.llm.provider import ComplaintLLMProvider, TokenLimitExceededError


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

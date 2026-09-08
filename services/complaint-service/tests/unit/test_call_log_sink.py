"""ENG-01 stretch：call_log tempfile sink（≠ Batch-D / ENG-02/07 / COMP-01/02）。"""
from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.config import settings
from app.llm.provider import ComplaintLLMProvider
from app.mcp.client import MCPClient
from app.observability.call_log_sink import TempFileCallLogSink, set_call_log_sink
from tests.mock.llm.mock_provider import MockLLMProvider
from tests.mock.mcp.mock_client import MockMCPClient


class _Schema(BaseModel):
    verdict: str = "pending"
    confidence: float = 0.5


@pytest.fixture(autouse=True)
def _clear_sink():
    set_call_log_sink(None)
    yield
    set_call_log_sink(None)


@pytest.mark.asyncio
async def test_llm_call_log_tempfile_sink(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
    path = tmp_path / "llm_calls.jsonl"
    set_call_log_sink(TempFileCallLogSink(path))
    provider = ComplaintLLMProvider(transport=MockLLMProvider())
    await provider.invoke("请给出判责结论", _Schema, trace_id="t-sink", session_id="s1")
    text = path.read_text(encoding="utf-8")
    assert "t-sink" in text
    assert '"channel": "llm"' in text or '"channel":"llm"' in text
    assert len(provider.call_log) >= 1


@pytest.mark.asyncio
async def test_mcp_call_log_tempfile_sink(tmp_path: Path):
    path = tmp_path / "mcp_calls.jsonl"
    set_call_log_sink(TempFileCallLogSink(path))
    client = MCPClient(transport=MockMCPClient())
    await client.call(
        "rag_kb",
        "search_regulations",
        {"query": "夜间催收"},
        trace_id="t-mcp",
        session_id="s1",
    )
    text = path.read_text(encoding="utf-8")
    assert "t-mcp" in text
    assert "rag_kb" in text
    assert '"channel": "mcp"' in text or '"channel":"mcp"' in text
    assert len(client.call_log) >= 1

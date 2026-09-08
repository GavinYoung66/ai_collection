"""MCP 真路径：gateway/bridge + fail-fast（Apply-3）。"""
from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from app.config import settings
from app.mcp.rag_kb_bridge import RagKbBridge, map_search_response_to_tool_hits
from app.mcp.real_transport import RealMCPTransport
from app.mcp.validate import MCPConfigError, validate_real_mcp_config


def test_validate_real_mcp_skips_when_mock(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
    monkeypatch.setattr(settings, "RAG_SERVICE_URL", "")
    validate_real_mcp_config()


def test_validate_real_mcp_fails_without_rag_url(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "RAG_SERVICE_URL", "change_me")
    with pytest.raises(MCPConfigError, match="RAG_SERVICE_URL"):
        validate_real_mcp_config()


def test_validate_real_mcp_fails_when_empty(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "RAG_SERVICE_URL", "")
    with pytest.raises(MCPConfigError, match="RAG_SERVICE_URL"):
        validate_real_mcp_config()


def test_validate_real_mcp_ok_with_url(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "RAG_SERVICE_URL", "http://rag-service:8000")
    validate_real_mcp_config()


def test_map_search_response_includes_doc_id():
    envelope = {
        "code": "0",
        "data": {
            "results": [
                {
                    "doc_id": "REG-100",
                    "title": "催收规范",
                    "content": "禁止夜间催收",
                    "score": 0.9,
                    "rerank_score": 0.95,
                }
            ]
        },
    }
    hits = map_search_response_to_tool_hits(envelope, tool="search_regulations")
    assert hits[0]["doc_id"] == "REG-100"
    assert "禁止夜间" in hits[0]["content_snippet"]


@pytest.mark.asyncio
async def test_rag_kb_bridge_httpx_contract():
    """httpx mock：bridge 调用 POST /api/v1/kb/search 并返回含 doc_id 的 list。"""

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/kb/search"
        body = request.read()
        assert b"regulation" in body or b"query" in body
        return httpx.Response(
            200,
            json={
                "code": "0",
                "message": "success",
                "data": {
                    "results": [
                        {
                            "doc_id": "DOC-BRIDGE-1",
                            "title": "法规A",
                            "content": "内容片段",
                            "score": 0.8,
                            "rerank_score": 0.81,
                        }
                    ]
                },
            },
        )

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="http://rag.test")
    bridge = RagKbBridge("http://rag.test", client=client)
    hits = await bridge.call_tool(
        "search_regulations", {"query": "夜间催收", "top_k": 5}, trace_id="t1"
    )
    await client.aclose()
    assert isinstance(hits, list)
    assert hits[0]["doc_id"] == "DOC-BRIDGE-1"


@pytest.mark.asyncio
async def test_real_transport_rag_kb_no_notimplemented():
    bridge = RagKbBridge("http://unused")
    bridge.call_tool = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"doc_id": "X1", "title": "t", "content_snippet": "c", "score": 1.0}]
    )
    t = RealMCPTransport(bridge=bridge, gateway_url="")
    out = await t.call("rag_kb", "search_regulations", {"query": "q"})
    assert out[0]["doc_id"] == "X1"


@pytest.mark.asyncio
async def test_real_transport_templates_tool():
    bridge = RagKbBridge("http://unused")
    bridge.call_tool = AsyncMock(return_value=[{"doc_id": "T1", "template_id": "T1"}])  # type: ignore[method-assign]
    t = RealMCPTransport(bridge=bridge)
    out = await t.call("rag_kb", "search_response_templates", {"query": "q"})
    assert out[0]["doc_id"] == "T1"


@pytest.mark.asyncio
async def test_guidelines_reserved_returns_empty():
    bridge = RagKbBridge("http://rag.test")
    out = await bridge.call_tool("search_complaint_handling_guidelines", {})
    assert out == []

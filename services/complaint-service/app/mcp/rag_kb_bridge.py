"""rag_kb → rag-service HTTP 桥接（design D2）。

图节点仍只调 ``mcp.call("rag_kb", tool, …)``；真路径在此发
``POST /api/v1/kb/search``，再映射为 Mock 契约同形的 list[dict]（含 ``doc_id``）。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
import structlog

from app.mcp.errors import MCPError, MCPTimeoutError

logger = structlog.get_logger()

# 支持工具 → 检索 category（rag SearchRequest 枚举）
_TOOL_CATEGORY = {
    "search_regulations": "regulation",
    "search_response_templates": "script",
}

# DEP-04：本里程碑图节点未挂接；显式 reserved-unused
_RESERVED_UNUSED_TOOLS = frozenset({"search_complaint_handling_guidelines"})


class RagKbBridge:
    """进程内 rag_kb 桥：HTTP 检索 → MCP tool 结果列表。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ):
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = timeout
        self._client = client

    async def call_tool(
        self,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        if tool in _RESERVED_UNUSED_TOOLS:
            logger.info(
                "rag_kb_tool_reserved_unused",
                tool=tool,
                trace_id=trace_id,
                session_id=session_id,
            )
            return []

        category = _TOOL_CATEGORY.get(tool)
        if category is None:
            raise MCPError("rag_kb", tool, f"unsupported rag_kb tool: {tool}")

        if not self._base_url:
            raise MCPError("rag_kb", tool, "RAG_SERVICE_URL not configured")

        query = _extract_query(params)
        top_k = int(params.get("top_k") or 5)
        url = f"{self._base_url}/api/v1/kb/search"
        payload = {
            "query": query,
            "category": category,
            "top_k": top_k,
            "rewrite": False,
            "answer": False,
        }
        headers = {}
        if trace_id:
            headers["X-Trace-Id"] = trace_id

        try:
            body = await self._post_json(url, payload, headers=headers)
        except httpx.TimeoutException as e:
            raise MCPTimeoutError("rag_kb", tool, f"rag search timeout: {e}") from e
        except httpx.HTTPError as e:
            raise MCPError("rag_kb", tool, f"rag search http error: {e}") from e

        return map_search_response_to_tool_hits(body, tool=tool)

    async def _post_json(
        self, url: str, payload: dict, *, headers: dict[str, str]
    ) -> dict:
        if self._client is not None:
            resp = await self._client.post(
                url, json=payload, headers=headers, timeout=self._timeout
            )
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise MCPError("rag_kb", "search", "invalid rag response type")
            return data

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            if not isinstance(data, dict):
                raise MCPError("rag_kb", "search", "invalid rag response type")
            return data


def _extract_query(params: dict) -> str:
    for key in ("query", "complaint_type", "verdict"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]
    return "complaint"


def map_search_response_to_tool_hits(
    envelope: dict, *, tool: str
) -> list[dict[str, Any]]:
    """将 rag ``{code,data:{results:[…]}}`` 映射为 MCP tool list（含 doc_id）。"""
    data = envelope.get("data") if isinstance(envelope.get("data"), dict) else envelope
    if not isinstance(data, dict):
        return []
    results = data.get("results") or []
    hits: list[dict[str, Any]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        doc_id = str(item.get("doc_id") or "")
        title = str(item.get("title") or "")
        content = str(item.get("content") or item.get("content_snippet") or "")
        score = item.get("rerank_score", item.get("score", 0.0))
        try:
            score_f = float(score) if score is not None else 0.0
        except (TypeError, ValueError):
            score_f = 0.0
        hit: dict[str, Any] = {
            "doc_id": doc_id,
            "title": title,
            "content_snippet": content[:500],
            "source_type": _TOOL_CATEGORY.get(tool, "other"),
            "score": score_f,
            "source_url": item.get("source_url") or "",
        }
        if tool == "search_response_templates":
            hit["template_id"] = doc_id or title
            hit["template_text"] = content[:2000]
        hits.append(hit)
    return hits

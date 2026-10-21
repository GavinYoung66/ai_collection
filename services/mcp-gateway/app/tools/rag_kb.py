"""rag_kb 工具实现（平移自 services/complaint-service/app/mcp/rag_kb_bridge.py）。

调用方仍只调 ``rag_kb.<tool>``；本网关在网关内发
``POST /api/v1/kb/search``，再映射为与 complaint-service Mock 契约同形的
list[dict]（含 ``doc_id``）。

平移差异（任务 2.4）：源桥自管 httpx POST；网关版复用 bridge_http 共享基座
（信封解包/404/超时错误映射统一收口），业务映射逻辑不变。
"""
from __future__ import annotations

from typing import Any

import structlog

from app.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp_errors import MCPError

logger = structlog.get_logger()

# 支持工具 → 检索 category（rag SearchRequest 枚举）
_TOOL_CATEGORY = {
    "search_regulations": "regulation",
    "search_response_templates": "script",
}

# DEP-04：本里程碑图节点未挂接；显式 reserved-unused
_RESERVED_UNUSED_TOOLS = frozenset({"search_complaint_handling_guidelines"})


class RagKbToolset:
    """rag_kb 工具集：HTTP 检索 → MCP tool 结果列表。"""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        # rag 检索含 ES+向量+rerank，源桥超时 30s（非共享基座默认 5s）
        self._http = BridgeHttpClient(base_url, timeout=timeout)
        self._base_url = (base_url or "").rstrip("/")

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
        payload = {
            "query": query,
            "category": category,
            "top_k": top_k,
            "rewrite": False,
            "answer": False,
        }

        # Batch 3：下游 5xx/超时（MCPError/MCPTimeoutError）不再就地吞掉，
        # 上抛由可靠性层分级重试；重试耗尽后路由 fallback 兜空列表
        try:
            # 共享基座已解包统一信封，返回 data（dict，含 results）
            data = await self._http.request(
                "POST",
                "/api/v1/kb/search",
                json_body=payload,
                trace_id=trace_id,
            )
        except BridgeNotFound:
            # 检索无结果码按查无处理：返空列表（对齐源桥容错语义；非故障，不计熔断）
            logger.info("rag_kb_search_not_found", tool=tool, trace_id=trace_id)
            return []

        return map_search_response_to_tool_hits(data, tool=tool)


def _extract_query(params: dict) -> str:
    for key in ("query", "complaint_type", "verdict"):
        val = params.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:500]
    return "complaint"


def map_search_response_to_tool_hits(
    envelope: Any, *, tool: str
) -> list[dict[str, Any]]:
    """将 rag 检索 data（或完整信封）映射为 MCP tool list（含 doc_id）。"""
    data = envelope.get("data") if isinstance(envelope, dict) and isinstance(envelope.get("data"), dict) else envelope
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

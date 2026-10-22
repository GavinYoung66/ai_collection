"""call_qc MCP Server 入口：list_tools + 两 handler。不引入 FastMCP。

投诉节点禁止直连 qc HTTP；本 Server 内部查 QualityRepo / AlertRepo
（等价于 Server 内 GET /qc/records、/qc/alerts）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.mcp.tools import (
    FORBIDDEN_TOOLS,
    TOOL_QUERY_QC_ALERTS,
    TOOL_QUERY_QC_VIOLATIONS,
    TOOL_SPECS,
    implemented_tool_names,
)
from app.repositories.alert_repo import AlertRepo
from app.repositories.quality_repo import QualityRepo
from common.security.mask import mask_text

SERVER_NAME = "call_qc"


def _as_unix(value: datetime | None) -> int | None:
    if value is None:
        return None
    dt = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
    return int(dt.timestamp())


def _case_matches(record: Any, case_id: str) -> bool:
    stored = getattr(record, "case_id", None)
    if stored is None:
        return True
    return str(stored) == str(case_id)


class CallQcServer:
    """薄 MCP 工具注册。无 query_call_asr / query_call_sessions。"""

    def __init__(
        self,
        *,
        quality_repo: QualityRepo | None = None,
        alert_repo: AlertRepo | None = None,
    ) -> None:
        self.quality_repo = quality_repo
        self.alert_repo = alert_repo
        self.server_name = SERVER_NAME

    def list_tools(self) -> list[dict[str, Any]]:
        return [dict(item) for item in TOOL_SPECS]

    def tool_names(self) -> tuple[str, ...]:
        return implemented_tool_names()

    async def call(self, tool: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        name = str(tool)
        if name in FORBIDDEN_TOOLS:
            raise ValueError(f"{name} is not implemented by qc-service call_qc")
        if name == TOOL_QUERY_QC_VIOLATIONS:
            return await self.query_qc_violations(params or {})
        if name == TOOL_QUERY_QC_ALERTS:
            return await self.query_qc_alerts(params or {})
        raise ValueError(f"unknown call_qc tool: {name}")

    async def query_qc_violations(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        record = await self._load_record(params)
        if record is None or self.quality_repo is None:
            return []
        rows = await self.quality_repo.list_violations(record.id)
        out: list[dict[str, Any]] = []
        for row in rows:
            hit_ts = getattr(row, "hit_ts", None) or getattr(row, "create_time", None)
            out.append(
                {
                    "qc_record_id": str(record.id),
                    "redline_tag": getattr(row, "redline_tag", None),
                    "violation_rule_id": getattr(row, "rule_id", None),
                    "snippet_text": mask_text(getattr(row, "evidence_text", None) or ""),
                    "points": getattr(row, "points", None),
                    "policy_clause": getattr(row, "policy_clause", None),
                    "policy_text": getattr(row, "policy_text", None),
                    "ts": _as_unix(hit_ts),
                }
            )
        return out

    async def query_qc_alerts(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        record = await self._load_record(params)
        if record is None or self.alert_repo is None:
            return []
        session_id = str(params.get("session_id") or "")
        items, _total = await self.alert_repo.list_alerts(
            page=1,
            page_size=100,
            session_id=session_id or None,
        )
        wanted = str(record.id)
        out: list[dict[str, Any]] = []
        for item in items:
            qc_record_id = str(item.get("qc_record_id") or "")
            if qc_record_id != wanted:
                continue
            evidence = mask_text(str(item.get("evidence") or ""))
            out.append(
                {
                    "qc_record_id": qc_record_id,
                    "alert_id": str(item.get("alert_id") or ""),
                    "evidence": evidence,
                }
            )
        return out

    async def _load_record(self, params: dict[str, Any]) -> Any | None:
        case_id = str(params.get("case_id") or "")
        session_id = str(params.get("session_id") or "")
        if not case_id or not session_id or self.quality_repo is None:
            return None
        record = await self.quality_repo.get_by_session_id(session_id)
        if record is None:
            return None
        if not _case_matches(record, case_id):
            return None
        return record

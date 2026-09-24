"""business_ticket 进程内桥（mcp-real-path / design D5）。

图节点仍只调 ``mcp.call("business_ticket", tool, …)``；本桥：
- query_case_info：data-platform 快照 → fixture 形状；快照 404 fallback 读 public.cases
- query_history_tickets / query_history_complaints：本闭环无历史表，恒返 []

容错（spec mcp-real-path 4.2）：查不到或下游故障（404/5xx/超时/DB 错）
一律返默认 fixture（零值字段，不伪造数据）并记可查日志，图继续执行、不裸返 500；
仅对编程错误（不支持的 tool、缺 case_id）抛 MCPError 快速失败。
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import structlog
from sqlalchemy import text

from common.security.mask import mask_name
from app.mcp.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp.errors import MCPError, MCPTimeoutError

logger = structlog.get_logger()

_SHANGHAI = ZoneInfo("Asia/Shanghai")
_SERVER = "business_ticket"

_SUPPORTED_TOOLS = frozenset(
    {"query_case_info", "query_history_tickets", "query_history_complaints"}
)

# 快照 404 时的兜底：cases 表无 product_type 列，不伪造；只取真实存在的列。
_SQL_CASE_FALLBACK = text(
    """
    SELECT id::text AS case_id,
           customer_name,
           overdue_amount,
           overdue_days,
           create_time
    FROM cases
    WHERE id = CAST(:case_id AS uuid)
      AND is_delete = '0'
    """
)


class BusinessTicketBridge:
    """business_ticket server 进程内真桥。"""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._http = BridgeHttpClient(base_url, client=client)

    async def call_tool(
        self,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> Any:
        if tool not in _SUPPORTED_TOOLS:
            raise MCPError(_SERVER, tool, f"unsupported business_ticket tool: {tool}")

        if tool != "query_case_info":
            # 本闭环不新建历史表：查无历史即空列表
            logger.info("business_ticket_history_empty", tool=tool, trace_id=trace_id)
            return []

        case_id = str((params or {}).get("case_id") or "")
        if not case_id:
            raise MCPError(_SERVER, tool, "query_case_info requires case_id")

        try:
            snap = await self._http.request(
                "GET",
                f"/api/v1/data/snapshot/{case_id}",
                trace_id=trace_id,
            )
            return _map_snapshot_to_fixture(case_id, snap)
        except BridgeNotFound:
            logger.info(
                "case_snapshot_miss_fallback_cases", case_id=case_id, trace_id=trace_id
            )
            return await _fallback_read_cases(case_id, trace_id=trace_id)
        except (MCPTimeoutError, MCPError) as e:
            # 4.2：data-platform 故障（5xx/超时）→ 默认 fixture（零值，不伪造），图继续执行
            logger.warning(
                "case_snapshot_error_default_fixture",
                case_id=case_id,
                error=str(e),
                trace_id=trace_id,
            )
            return _default_case_fixture(case_id)


def _map_snapshot_to_fixture(case_id: str, snap: Any) -> dict[str, Any]:
    """快照 14 列 → fixture 形状。

    快照表无 product_type/create_time 列：给空串/None，禁止编造。
    customer_name 在快照中已是脱敏值，直接使用。
    """
    snap = snap if isinstance(snap, dict) else {}
    return {
        "case_id": str(snap.get("case_id") or case_id),
        "overdue_amount": _num(snap.get("overdue_amount")),
        "overdue_days": _int(snap.get("overdue_days")),
        "repayment_records": [],
        "customer_name": str(snap.get("customer_name") or ""),
        "product_type": "",
        "create_time": snap.get("create_time"),
    }


def _default_case_fixture(case_id: str) -> dict[str, Any]:
    """4.2 容错默认值：零值字段、空串/空列表/None，禁止伪造业务数据。"""
    return {
        "case_id": case_id,
        "overdue_amount": 0.0,
        "overdue_days": 0,
        "repayment_records": [],
        "customer_name": "",
        "product_type": "",
        "create_time": None,
    }


async def _fallback_read_cases(case_id: str, *, trace_id: str) -> dict[str, Any]:
    """快照查不到：fallback 读 public.cases 同一行。

    DB 故障或 cases 表同样查无 → 返默认 fixture（不抛 500，spec 4.2），
    由日志保留可审计痕迹；后续研判节点按证据缺失处理。
    """
    from common.db.session import async_session_factory

    try:
        async with async_session_factory() as db:
            result = await db.execute(_SQL_CASE_FALLBACK, {"case_id": case_id})
            row = result.mappings().first()
    except Exception as e:
        logger.warning(
            "case_fallback_db_error_default_fixture",
            case_id=case_id,
            error=str(e),
            trace_id=trace_id,
        )
        return _default_case_fixture(case_id)

    if row is None:
        logger.warning("case_not_found_in_snapshot_or_cases", case_id=case_id, trace_id=trace_id)
        return _default_case_fixture(case_id)

    return {
        "case_id": str(row["case_id"]),
        "overdue_amount": _num(row["overdue_amount"]),
        "overdue_days": _int(row["overdue_days"]),
        "repayment_records": [],
        # cases.customer_name 为明文，出桥前必须脱敏
        "customer_name": mask_name(str(row["customer_name"] or "")),
        # public.cases 无 product_type 列
        "product_type": "",
        "create_time": _iso_shanghai(row["create_time"]),
    }


def _num(value: Any) -> float:
    if value is None:
        return 0.0
    if isinstance(value, Decimal):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _iso_shanghai(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(_SHANGHAI).isoformat()

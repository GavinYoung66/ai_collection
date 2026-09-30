"""business_ticket 工具实现（平移自 services/complaint-service/app/mcp/business_ticket_bridge.py）。

- query_case_info：data-platform 快照 → fixture 形状
- query_history_tickets / query_history_complaints：本闭环无历史表，恒返 []

容错（对齐源桥 spec mcp-real-path 4.2）：查不到或下游故障（404/5xx/超时）
一律返默认 fixture（零值字段，不伪造数据）并记可查日志，图继续执行、不裸返 500；
仅对编程错误（不支持的 tool、缺 case_id）抛 MCPError 快速失败。

平移差异：源桥快照 404 时 fallback 直连 PG 读 public.cases（complaint-service
进程内 DB）。网关不跨服务直连他库，404 按查无返默认 fixture；spec 4.2 容错
语义不变（均为"查无 → 默认 fixture"，仅少了 cases 兜底数据源）。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any

import structlog

from app.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp_errors import MCPError

logger = structlog.get_logger()

_SERVER = "business_ticket"

_SUPPORTED_TOOLS = frozenset(
    {"query_case_info", "query_history_tickets", "query_history_complaints"}
)


class BusinessTicketToolset:
    """business_ticket server 工具集（HTTP 调 data-platform 快照接口）。"""

    def __init__(self, base_url: str) -> None:
        self._http = BridgeHttpClient(base_url)

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
            # 平移说明：源桥此处 fallback 读 public.cases；网关不跨库，按查无返默认 fixture
            # （查无数据非故障：不重试、不计熔断）
            logger.warning(
                "case_snapshot_miss_default_fixture", case_id=case_id, trace_id=trace_id
            )
            return _default_case_fixture(case_id)
        # Batch 3：data-platform 5xx/超时（MCPError/MCPTimeoutError）上抛，
        # 由可靠性层分级重试；耗尽后路由 fallback 兜 _default_case_fixture


def _map_snapshot_to_fixture(case_id: str, snap: Any) -> dict[str, Any]:
    """快照 17 列 → fixture 形状。

    0014 后快照表含 product_type/repayment_records/create_time 三列，
    从快照读实际业务值，不再固定填空串/空列表。
    仅当快照行对应列确为 NULL 时才返回空值（spec mcp-real-path）。
    customer_name 在快照中已是脱敏值，直接使用。
    """
    snap = snap if isinstance(snap, dict) else {}
    return {
        "case_id": str(snap.get("case_id") or case_id),
        "overdue_amount": _num(snap.get("overdue_amount")),
        "overdue_days": _int(snap.get("overdue_days")),
        "repayment_records": snap.get("repayment_records") or [],
        "customer_name": str(snap.get("customer_name") or ""),
        "product_type": str(snap.get("product_type") or ""),
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

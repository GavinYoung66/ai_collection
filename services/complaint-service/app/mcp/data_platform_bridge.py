"""data_platform 回写进程内桥（mcp-real-path / design D5、D7）。

sample_backflow 节点仍只调 ``mcp.call("data_platform", tool, payload)``，
入参只有研判 session_id、task_status、verdict。本桥：
- 进程内反查 session_id → complaint_id（SessionService）→ case_id（ComplaintService）
- PUT data-platform 内部回写口更新快照投诉三列（只覆盖已存在行）
- write_session / write_human_review_truth 在本闭环写同一批投诉列；
  快照表无独立人审结论列，不新增列
- 回写连续失败 3 次：数据入 csmp:backflow:retry（A5013），返回成功形状不抛异常，
  工单状态正常推进、快照投诉列保持空
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Awaitable, Callable, Optional

import httpx
import structlog

from common.errors.codes import ErrorCode
from common.security.audit import log_action
from app.config import settings
from app.mcp.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp.errors import MCPError
from app.utils.redis_keys import backflow_retry_key

logger = structlog.get_logger()

_SERVER = "data_platform"
_SUPPORTED_TOOLS = frozenset({"write_session", "write_human_review_truth"})
_TOOL_KIND = {
    "write_session": "session",
    "write_human_review_truth": "human_review_truth",
}

# resolver：研判 session_id → (complaint_id, case_id)；默认走两个进程内单例反查
Resolver = Callable[[str], Awaitable[Optional[tuple[str, str]]]]


async def _default_resolver(session_id: str) -> Optional[tuple[str, str]]:
    from app.service.complaint_service import complaint_service
    from app.service.session_service import session_service

    session = session_service.get_session(session_id)
    if not session:
        return None
    complaint_id = str(session.get("complaint_id") or "")
    if not complaint_id:
        return None
    rec = complaint_service._cache.get(complaint_id)  # noqa: SLF001 — 进程内只读反查
    if not rec:
        return None
    case_id = str(rec.get("case_id") or "")
    if not case_id:
        return None
    return complaint_id, case_id


class DataPlatformBridge:
    """data_platform server 进程内真桥（投诉列回写）。"""

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        resolver: Resolver | None = None,
        redis_client: Any = None,
        max_retry: Optional[int] = None,
    ) -> None:
        self._http = BridgeHttpClient(base_url, client=client)
        self._resolver = resolver or _default_resolver
        self._redis = redis_client  # None → 懒取 common resilience 共享 Redis
        self._max_retry = max_retry or int(getattr(settings, "BRIDGE_HTTP_RETRY", 3))

    async def call_tool(
        self,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> dict[str, Any]:
        if tool not in _SUPPORTED_TOOLS:
            raise MCPError(_SERVER, tool, f"unsupported data_platform tool: {tool}")

        params = params or {}
        sid = str(params.get("session_id") or session_id or "")
        task_status = str(params.get("task_status") or "")
        verdict = str(params.get("verdict") or "")
        kind = _TOOL_KIND[tool]

        if not sid:
            raise MCPError(_SERVER, tool, "write requires session_id")

        resolved = await self._resolver(sid)
        if resolved is None:
            # 进程内反查不到 case_id：无法定位快照行；不阻塞工单，记可查日志
            logger.warning(
                "backflow_resolve_miss", tool=tool, session_id=sid, trace_id=trace_id
            )
            return {"success": True}

        if not verdict or not task_status:
            # 回写口要求三列非空；缺字段属永久性数据缺口，重试无意义，不抛异常
            logger.warning(
                "backflow_skip_empty_fields",
                tool=tool,
                session_id=sid,
                trace_id=trace_id,
            )
            return {"success": True}

        complaint_id, case_id = resolved
        body = {
            "complaint_id": complaint_id,
            "verdict": verdict,
            "task_status": task_status,
        }

        last_error = ""
        for attempt in range(1, self._max_retry + 1):
            try:
                await self._http.request(
                    "PUT",
                    f"/api/v1/data/snapshot/{case_id}",
                    json_body=body,
                    trace_id=trace_id,
                )
                logger.info(
                    "backflow_write_ok",
                    tool=tool,
                    session_id=sid,
                    case_id=case_id,
                    attempt=attempt,
                    trace_id=trace_id,
                )
                return {"success": True}
            except BridgeNotFound as e:
                # 快照行不存在：重试也不会出现，直接走失败入队
                last_error = f"snapshot row not found: {e}"
                break
            except MCPError as e:
                last_error = str(e)
                logger.warning(
                    "backflow_write_retry",
                    tool=tool,
                    session_id=sid,
                    case_id=case_id,
                    attempt=attempt,
                    error=last_error,
                )
                if attempt < self._max_retry:
                    await asyncio.sleep(0)

        # 重试用尽（或快照行缺失）：零丢失入队 + A5013，不抛异常 → 工单正常推进
        await self._enqueue_failed(kind, sid, body, last_error, trace_id=trace_id)
        return {"success": True}

    async def _enqueue_failed(
        self,
        kind: str,
        session_id: str,
        data: dict[str, Any],
        error: str,
        *,
        trace_id: str,
    ) -> None:
        payload = json.dumps(
            {
                "kind": kind,
                "session_id": session_id,
                "data": data,
                "error_code": ErrorCode.A5013.value,
                "error": error[:300],
            },
            ensure_ascii=False,
        )
        try:
            redis = self._redis
            if redis is None:
                from common.resilience._redis import get_redis

                redis = get_redis()
            await redis.lpush(backflow_retry_key(), payload)
        except Exception:
            logger.exception(
                "backflow_retry_enqueue_failed",
                session_id=session_id,
                trace_id=trace_id,
            )

        log_action(
            action="backflow_failed",
            resource=f"{kind}/{session_id}",
            error_code=ErrorCode.A5013.value,
            trace_id=trace_id,
        )
        logger.error(
            "backflow_failed",
            error_code=ErrorCode.A5013.value,
            kind=kind,
            session_id=session_id,
            error=error,
            trace_id=trace_id,
        )

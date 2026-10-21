"""data_platform 回写工具实现（平移自 services/complaint-service/app/mcp/data_platform_bridge.py）。

sample_backflow 节点仍只调 ``data_platform.<tool>``，入参只有研判 session_id、
task_status、verdict。本网关工具：
- 反查 session_id → (complaint_id, case_id)：源桥走 complaint-service 进程内
  SessionService/ComplaintService 单例；网关跨进程无法复用，改为
  ① params 直接携带 complaint_id/case_id（调用方已知的场景），或
  ② 注入式 resolver（部署方接线；未接线时 resolve miss → persisted=False，
     记可查日志，不阻塞工单）
- PUT data-platform 内部回写口更新快照投诉三列（只覆盖已存在行）
- write_session / write_human_review_truth 在本闭环写同一批投诉列；
  快照表无独立人审结论列，不新增列
- 回写失败零丢失：入 csmp:backflow:retry（A5013），返回成功形状不抛异常，
  工单状态正常推进、快照投诉列保持空

Batch 3（可靠性层迁移）平移差异：
- 回写重试由源桥内部循环（BRIDGE_HTTP_RETRY=3）收归网关可靠性编排层统一执行
  （业务错重试 3 次指数退避，单层实现，SHALL NOT 与下游重试叠加）；
  本模块单次调用只发一次 PUT，失败抛 _BackflowFailed（携带入队上下文）；
- 重试耗尽后由路由 fallback 调 self.fallback() 完成入队 + 成功形状返回；
- 快照行 404（永久性缺口）不重试、不计熔断，就地走 fallback 入队；
- 失败入队经懒导入 common.resilience redis，网关运行时若 redis 客户端
  不可用仅记日志（与源桥 try/except 兜底语义一致）。
"""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Optional

import structlog

from app.bridge_http import BridgeHttpClient, BridgeNotFound
from app.mcp_errors import MCPError

logger = structlog.get_logger()

_SERVER = "data_platform"
_SUPPORTED_TOOLS = frozenset({"write_session", "write_human_review_truth"})
_TOOL_KIND = {
    "write_session": "session",
    "write_human_review_truth": "human_review_truth",
}

# 与 complaint-service app/utils/redis_keys.backflow_retry_key() 同值
_BACKFLOW_RETRY_KEY = "csmp:backflow:retry"
# A5013 回写失败（对齐 common.errors.codes.ErrorCode.A5013）
_ERR_BACKFLOW = "A5013"

# resolver：研判 session_id → (complaint_id, case_id)
Resolver = Callable[[str], Awaitable[Optional[tuple[str, str]]]]


async def _unresolved(session_id: str) -> Optional[tuple[str, str]]:
    """默认 resolver：网关跨进程无法访问 complaint-service 进程内会话单例。

    未接线 resolver 且 params 未携带 complaint_id/case_id 时返回 None，
    走 resolve-miss 容错路径（persisted=False + 可查日志）。
    """
    return None


class _BackflowFailed(MCPError):
    """回写单次尝试失败：携带入队上下文（kind/session_id/body/ids），

    供可靠性层重试（业务错 3 次）及耗尽后的路由 fallback 入队使用。
    """

    def __init__(self, tool: str, message: str, *, enqueue_ctx: dict[str, Any]):
        super().__init__(_SERVER, tool, message)
        self.enqueue_ctx = enqueue_ctx


class DataPlatformToolset:
    """data_platform server 工具集（投诉列回写，HTTP 调 data-platform 快照 PUT 口）。"""

    def __init__(
        self,
        base_url: str,
        *,
        resolver: Resolver | None = None,
    ) -> None:
        self._http = BridgeHttpClient(base_url)
        self._resolver = resolver or _unresolved

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

        # ① params 直带 complaint_id/case_id 优先；② 注入式 resolver 反查
        complaint_id = str(params.get("complaint_id") or "")
        case_id = str(params.get("case_id") or "")
        if not (complaint_id and case_id):
            resolved = await self._resolver(sid)
            if resolved is None:
                # 反查不到 case_id：无法定位快照行；不阻塞工单，记可查日志
                logger.warning(
                    "backflow_resolve_miss", tool=tool, session_id=sid, trace_id=trace_id
                )
                return {"success": True, "persisted": False}
            complaint_id, case_id = resolved

        if not verdict or not task_status:
            # 回写口要求三列非空；缺字段属永久性数据缺口，重试无意义，不抛异常
            logger.warning(
                "backflow_skip_empty_fields",
                tool=tool,
                session_id=sid,
                trace_id=trace_id,
            )
            return {"success": True, "persisted": False}

        body = {
            "complaint_id": complaint_id,
            "verdict": verdict,
            "task_status": task_status,
        }
        enqueue_ctx = {
            "kind": kind,
            "session_id": sid,
            "body": body,
            "case_id": case_id,
            "complaint_id": complaint_id,
        }

        # Batch 3：单次调用只发一次 PUT；重试由可靠性编排层统一执行（单层）。
        try:
            await self._http.request(
                "PUT",
                f"/api/v1/data/snapshot/{case_id}",
                json_body=body,
                trace_id=trace_id,
            )
        except BridgeNotFound as e:
            # 快照行不存在：永久性缺口，重试也不会出现；不计熔断，就地入队
            return await self.fallback(tool, params, trace_id=trace_id, error=e, enqueue_ctx=enqueue_ctx)
        except MCPError as e:
            # 5xx/超时/传输错：携带入队上下文上抛，可靠性层按业务错重试
            raise _BackflowFailed(tool, str(e), enqueue_ctx=enqueue_ctx) from e

        logger.info(
            "backflow_write_ok",
            tool=tool,
            session_id=sid,
            case_id=case_id,
            trace_id=trace_id,
        )
        # persisted=True：快照投诉列确已写入（5.5 result.ready 门槛）
        return {
            "success": True,
            "persisted": True,
            "case_id": case_id,
            "complaint_id": complaint_id,
        }

    async def fallback(
        self,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        error: BaseException | None = None,
        enqueue_ctx: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """容错兜底（可靠性层业务错重试耗尽 / 快照行 404）：零丢失入队 + 成功形状。

        不抛异常 → 工单正常推进，快照投诉列保持空（spec mcp-real-path「回写失败不阻塞工单」）。
        """
        ctx = enqueue_ctx or getattr(error, "enqueue_ctx", None) or {}
        kind = str(ctx.get("kind") or _TOOL_KIND.get(tool, tool))
        sid = str(ctx.get("session_id") or (params or {}).get("session_id") or "")
        body = ctx.get("body") or {
            "complaint_id": str((params or {}).get("complaint_id") or ""),
            "verdict": str((params or {}).get("verdict") or ""),
            "task_status": str((params or {}).get("task_status") or ""),
        }
        case_id = str(ctx.get("case_id") or (params or {}).get("case_id") or "")
        complaint_id = str(ctx.get("complaint_id") or (params or {}).get("complaint_id") or "")
        await self._enqueue_failed(kind, sid, body, str(error or "")[:300], trace_id=trace_id)
        return {
            "success": True,
            "persisted": False,
            "case_id": case_id,
            "complaint_id": complaint_id,
        }

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
                "error_code": _ERR_BACKFLOW,
                "error": error[:300],
            },
            ensure_ascii=False,
        )
        try:
            # 懒导入：网关容器未装 redis 客户端时仅记日志（源桥同为 try/except 兜底）
            from common.resilience._redis import get_redis

            redis = get_redis()
            await redis.lpush(_BACKFLOW_RETRY_KEY, payload)
        except Exception:
            logger.exception(
                "backflow_retry_enqueue_failed",
                session_id=session_id,
                trace_id=trace_id,
            )

        logger.error(
            "backflow_failed",
            error_code=_ERR_BACKFLOW,
            kind=kind,
            session_id=session_id,
            error=error,
            trace_id=trace_id,
        )

"""MCP Client 统一调用封装。

职责：
- 统一 `call(server, tool, params) -> dict` 入口
- 可靠性编排委托 `common.mcp.reliability.call_tool`：三级超时（total=30s）、
  错误分级（超时/权限拒绝不重试，业务错重试 3 次）、per-server 熔断
  （key=csmp:mcp_degraded:{server}，连续 5 次失败 open，30s 后放 half-open 探针）
- 调用审计写入 mcp_calls 结构（对齐 complaint_agent_logs.mcp_calls）
- 返回前 PII 脱敏二次校验（复用 common.security.mask）

【REQ-CJ-002】禁止直连业务库/内部 HTTP，所有外部能力只走 MCP。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from common.mcp.reliability import (
    MCPCircuitOpenError,
    call_tool as reliability_call_tool,
    set_mcp_transport,
)
from common.resilience.timeout import Timeout
from common.security.audit import log_action
from common.security.mask import mask_text

from app.mcp.errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError
from app.service.security import has_plaintext_pii

logger = structlog.get_logger()

_DEFAULT_TIMEOUT = 30.0

# 调用上下文（trace_id/session_id）经 payload 保留键透传给适配器，
# 避免为 per-call 上下文重注册全局 transport（与在途调用竞态）。
_CTX_KEY = "__ctx__"


class MCPClient:
    """MCP Client 统一调用封装。

    Args:
        transport: 底层 MCP 调用实现（默认批次0 MockMCPClient；prod 由 gateway 注入真实实现）。
        timeout: 单次调用总超时秒数（默认 30s，透传给 reliability 层 Timeout(total=...)）。

    约束：reliability 层 transport 为全局单槽（set_mcp_transport），本服务经
    deps.get_mcp_client 单例使用；多实例并存时以最后注册者为准。
    """

    def __init__(self, transport: Any | None = None, timeout: float = _DEFAULT_TIMEOUT):
        self._transport = transport
        self._timeout = timeout
        self._call_log: list[dict] = []
        # G6：注册 reliability 层 transport 适配器（单例场景仅首次生效）
        set_mcp_transport(self._reliability_transport)

    @property
    def call_log(self) -> list[dict]:
        """审计日志快照。"""
        return list(self._call_log)

    def _get_transport(self) -> Any:
        """懒加载默认 transport（批次0 MockMCPClient）。"""
        if self._transport is None:
            from tests.mock.mcp.mock_client import MockMCPClient

            self._transport = MockMCPClient()
        return self._transport

    async def _reliability_transport(  # noqa: ARG002 — 超时由 reliability wait_for(total) 权威控制
        self, server: str, tool: str, payload: dict, timeout_seconds: float
    ) -> Any:
        """reliability 层 transport 适配器：payload 保留键还原调用上下文。

        app 层异常 → reliability 原语映射：
        - MCPTimeoutError → asyncio.TimeoutError（不重试，计熔断失败）
        - MCPPermissionDeniedError → PermissionError（不重试，不计失败）
        - MCPError → 透传（业务错，reliability 内部重试 3 次）
        """
        payload = dict(payload) if isinstance(payload, dict) else {}
        ctx = payload.pop(_CTX_KEY, None) or {}
        try:
            return await self._get_transport().call(
                server,
                tool,
                payload,
                trace_id=str(ctx.get("trace_id") or ""),
                session_id=str(ctx.get("session_id") or ""),
            )
        except MCPTimeoutError as e:
            raise asyncio.TimeoutError(str(e)) from e
        except MCPPermissionDeniedError as e:
            raise PermissionError(str(e)) from e

    async def call(
        self,
        server: str,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> Any:
        """调用 MCP tool，返回 dict。

        可靠性（超时/分级重试/per-server 熔断）由 reliability 层编排；
        本方法保留：调用审计、PII 脱敏二次校验、app 级异常映射。
        """
        start = time.monotonic()
        payload = dict(params) if isinstance(params, dict) else {}
        payload[_CTX_KEY] = {"trace_id": trace_id, "session_id": session_id}
        # 维3 per-server 令牌桶（spec rate-limit 维度3）：超限直接拒绝、不触达 server，
        # csmp.rate_limit.hit 事件（含 node/trace_id）由 rate_limit 零件层发出。
        # Redis 故障时零件层 fail-open 放行，不阻断 MCP 调用。
        from app.service.rate_limiter import rate_limiter

        if not await rate_limiter.acquire_mcp(server, trace_id=trace_id):
            self._record_audit(
                server, tool, params, None, trace_id, session_id, start, 1,
                error="rate_limited",
            )
            raise MCPError(server, tool, "MCP server 令牌桶耗尽，请稍后重试")
        try:
            result = await reliability_call_tool(
                server,
                tool,
                payload,
                timeout=Timeout(total=float(self._timeout)),
                breaker_key=f"csmp:mcp_degraded:{server}",
                context={"trace_id": trace_id, "node": f"mcp:{server}"},
            )
        except asyncio.TimeoutError as e:
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error="timeout")
            raise MCPTimeoutError(server, tool, f"timeout after {self._timeout}s") from e
        except PermissionError as e:
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error="permission_denied")
            logger.warning("mcp_permission_denied", server=server, tool=tool, trace_id=trace_id)
            raise MCPPermissionDeniedError(server, tool, str(e)) from e
        except MCPCircuitOpenError as e:
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error="circuit_open")
            logger.warning("mcp_circuit_open", server=server, tool=tool, trace_id=trace_id)
            raise
        except MCPError as e:
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error=f"business_error: {e}")
            raise
        except Exception as e:  # 未分类异常按服务故障处理
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error=f"unexpected: {e}")
            raise MCPError(server, tool, f"unexpected: {e}") from e

        # PII 脱敏二次校验：命中明文→脱敏 + 告警 + 审计
        if self._contains_pii(result):
            logger.warning(
                "mcp_pii_leak_detected",
                server=server, tool=tool, trace_id=trace_id,
            )
            log_action(
                action="mcp_pii_leak_sanitized",
                resource=f"{server}/{tool}",
                trace_id=trace_id,
            )
        sanitized = self._sanitize_result(result)
        self._record_audit(server, tool, params, sanitized, trace_id, session_id, start, 1, error=None)
        return sanitized

    def _sanitize_result(self, result: Any) -> Any:
        """PII 脱敏二次校验：检测明文身份证/手机号/银行卡并脱敏。"""
        if isinstance(result, str):
            return mask_text(result)
        if isinstance(result, dict):
            return {k: self._sanitize_result(v) for k, v in result.items()}
        if isinstance(result, list):
            return [self._sanitize_result(item) for item in result]
        return result

    @staticmethod
    def _contains_pii(result: Any) -> bool:
        """递归检测返回结构中是否含明文 PII（用于触发告警+审计）。"""
        if isinstance(result, str):
            return has_plaintext_pii(result)
        if isinstance(result, dict):
            return any(MCPClient._contains_pii(v) for v in result.values())
        if isinstance(result, list):
            return any(MCPClient._contains_pii(item) for item in result)
        return False

    def _record_audit(
        self,
        server: str,
        tool: str,
        params: dict,
        result: Any,
        trace_id: str,
        session_id: str,
        start: float,
        attempt: int,
        error: Optional[str],
    ) -> None:
        """记录调用审计（结构对齐 complaint_agent_logs.mcp_calls JSONB）。

        重试由 reliability 层内部消化，此处按调用（非按 attempt）记录最终结果。
        """
        latency_ms = int((time.monotonic() - start) * 1000)
        ended_at = datetime.now(timezone.utc)
        started_at = ended_at - timedelta(milliseconds=max(latency_ms, 0))
        entry = {
            "server": server,
            "tool": tool,
            "params": self._summary(params),
            "trace_id": trace_id,
            "session_id": session_id,
            "attempt": attempt,
            "latency_ms": latency_ms,
            "started_at": started_at,
            "ended_at": ended_at,
            "status": "error" if error else "success",
            "error": error,
        }
        self._call_log.append(entry)
        if error:
            logger.warning("mcp_call_audit", **entry)
        else:
            logger.debug("mcp_call_audit", **entry)
        try:
            from app.observability.call_log_sink import emit_call_log

            emit_call_log("mcp", entry)
        except Exception:
            pass

    @staticmethod
    def _summary(params: Any) -> Any:
        """参数摘要：dict 保留 key，value 截断。"""
        if isinstance(params, dict):
            return {k: (str(v)[:80] if v is not None else None) for k, v in params.items()}
        return str(params)[:80] if params is not None else None

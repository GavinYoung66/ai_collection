"""MCP Client 统一调用封装（Agent 侧薄封装）。

Batch 5（mcp-gateway-standalone）职责边界：
- 统一 ``call(server, tool, params) -> dict`` 入口，Graph 节点无感知
- 薄超时：``asyncio.wait_for``（默认 30s），网关挂了快速失败、不重试
- 可靠性编排（per-server 熔断 / 业务错重试 3 次 / per-server 限流）已迁移
  独立 mcp-gateway，Agent 侧 SHALL NOT 再做进程内编排（design D3，重试单层）
- 调用审计写入 mcp_calls 结构（对齐 complaint_agent_logs.mcp_calls）
- 返回前 PII 脱敏二次校验（复用 common.security.mask，defense-in-depth）

【REQ-CJ-002】禁止直连业务库/内部 HTTP，所有外部能力只走 MCP 网关。
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from common.security.audit import log_action
from common.security.mask import mask_text

from app.mcp.errors import MCPError, MCPPermissionDeniedError, MCPTimeoutError
from app.service.security import has_plaintext_pii

logger = structlog.get_logger()

_DEFAULT_TIMEOUT = 30.0


class MCPClient:
    """MCP Client 统一调用封装。

    Args:
        transport: 底层 MCP 调用实现。USE_MOCK_MCP=true 注入 MockMCPClient；
            USE_MOCK_MCP=false 由 gateway 注入 RealMCPTransport（MCP SDK client → 网关）。
        timeout: Agent 侧薄超时秒数（默认 30s）。网关自身 MCP total 上限也是 30s，
            Agent 侧 wait_for 保证网关无响应时快速失败、SHALL NOT 重试（重试单层在网关）。
    """

    def __init__(self, transport: Any | None = None, timeout: float = _DEFAULT_TIMEOUT):
        self._transport = transport
        self._timeout = timeout
        self._call_log: list[dict] = []

    @property
    def call_log(self) -> list[dict]:
        """审计日志快照。"""
        return list(self._call_log)

    async def aclose(self) -> None:
        """关闭底层 transport 持有的共享资源（如 HTTP 连接池）。

        透传到 transport 的 ``aclose``；transport 无此方法（如 Mock）时 no-op。
        供 main lifespan shutdown 调用，异常由调用方 suppress，不阻断关停。
        """
        transport = self._get_transport()
        close = getattr(transport, "aclose", None)
        if close is not None:
            await close()

    def _get_transport(self) -> Any:
        """懒加载默认 transport（批次0 MockMCPClient）。"""
        if self._transport is None:
            from tests.mock.mcp.mock_client import MockMCPClient

            self._transport = MockMCPClient()
        return self._transport

    async def call(
        self,
        server: str,
        tool: str,
        params: dict,
        *,
        trace_id: str = "",
        session_id: str = "",
    ) -> Any:
        """调用 MCP tool，返回 dict/list。

        Agent 侧仅保留：薄超时（wait_for 快速失败，不重试）→ transport.call()
        → PII 脱敏二次校验 → 调用审计。熔断/重试/限流由 mcp-gateway 承接。
        """
        start = time.monotonic()
        try:
            result = await asyncio.wait_for(
                self._get_transport().call(
                    server,
                    tool,
                    dict(params) if isinstance(params, dict) else {},
                    trace_id=trace_id,
                    session_id=session_id,
                ),
                timeout=self._timeout,
            )
        except MCPTimeoutError:
            # transport 已分类的网关/传输超时：不重试
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error="timeout")
            raise
        except asyncio.TimeoutError as e:
            # 薄超时触发：网关响应超过 self._timeout，取消等待、不重试
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error="timeout")
            raise MCPTimeoutError(server, tool, f"timeout after {self._timeout}s") from e
        except MCPPermissionDeniedError:
            # 权限拒绝：终止调用并写安全审计，不重试
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error="permission_denied")
            logger.warning("mcp_permission_denied", server=server, tool=tool, trace_id=trace_id)
            raise
        except MCPError as e:
            # 业务/服务故障（含网关返回的工具错误）：Agent 侧不重试，由节点既有降级处理
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error=f"business_error: {e}")
            raise
        except Exception as e:  # noqa: BLE001 — 未分类异常归一为 MCPError
            self._record_audit(server, tool, params, None, trace_id, session_id, start, 1, error=f"unexpected: {e}")
            raise MCPError(server, tool, f"unexpected: {e}") from e

        # PII 脱敏二次校验（defense-in-depth）：命中明文→脱敏 + 告警 + 审计。
        # 网关出口已做第一道脱敏，此处防止网关被绕过/漏网。
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
        """PII 脱敏二次校验：检测明文身份证/手机号/银行卡并递归脱敏。"""
        if isinstance(result, str):
            return mask_text(result)
        if isinstance(result, dict):
            return {k: self._sanitize_result(v) for k, v in result.items()}
        if isinstance(result, list):
            return [self._sanitize_result(item) for item in result]
        # 数字/布尔/None 等非字符串标量原样返回（PII 模式只匹配字符串形态）
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

        Agent 侧不重试，每次 call 恰记 1 条最终结果审计；网关侧另有完整审计链路。
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
        except Exception:  # noqa: BLE001 — 审计上报失败不阻断业务调用
            pass

    @staticmethod
    def _summary(params: Any) -> Any:
        """参数摘要：dict 保留 key，value 截断。"""
        if isinstance(params, dict):
            return {k: (str(v)[:80] if v is not None else None) for k, v in params.items()}
        return str(params)[:80] if params is not None else None

"""MCP Client 统一调用封装。

职责：
- 统一 `call(server, tool, params) -> dict` 入口
- 超时控制、分级重试（仅业务类错误）、服务故障不重试、权限拒绝终止+审计
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

from common.security.audit import log_action
from common.security.mask import mask_text
from app.mcp.errors import MCPPermissionDeniedError, MCPError, MCPTimeoutError
from app.service.security import has_plaintext_pii

logger = structlog.get_logger()

# 重试配置：仅业务类错误重试，最多 3 次指数退避
_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
_DEFAULT_TIMEOUT = 30.0


class MCPClient:
    """MCP Client 统一调用封装。

    Args:
        transport: 底层 MCP 调用实现（默认批次0 MockMCPClient；prod 由 gateway 注入真实实现）。
        timeout: 单次调用超时秒数。
    """

    def __init__(self, transport: Any | None = None, timeout: float = _DEFAULT_TIMEOUT):
        self._transport = transport
        self._timeout = timeout
        self._call_log: list[dict] = []

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

        错误分级：
        - MCPTimeoutError：服务故障，不重试，直接降级
        - MCPPermissionDeniedError：权限拒绝，终止并安全审计
        - MCPError（其它）：业务类错误，可重试最多 3 次
        """
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._get_transport().call(
                        server,
                        tool,
                        params,
                        trace_id=trace_id,
                        session_id=session_id,
                    ),
                    timeout=self._timeout,
                )
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
                self._record_audit(server, tool, params, sanitized, trace_id, session_id, start, attempt, error=None)
                return sanitized
            except asyncio.TimeoutError:
                last_exc = MCPTimeoutError(server, tool, f"timeout after {self._timeout}s")
                self._record_audit(server, tool, params, None, trace_id, session_id, start, attempt, error="timeout")
                # 服务故障：不重试，直接降级
                break
            except MCPPermissionDeniedError as e:
                last_exc = e
                self._record_audit(server, tool, params, None, trace_id, session_id, start, attempt, error="permission_denied")
                logger.warning("mcp_permission_denied", server=server, tool=tool, trace_id=trace_id)
                # 权限拒绝：终止，不重试
                break
            except MCPTimeoutError as e:
                last_exc = e
                self._record_audit(server, tool, params, None, trace_id, session_id, start, attempt, error="timeout")
                break
            except MCPError as e:
                last_exc = e
                self._record_audit(server, tool, params, None, trace_id, session_id, start, attempt, error=f"business_error: {e}")
                # 业务类错误：可重试
                if attempt < _MAX_RETRIES:
                    delay = _RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    logger.info("mcp_retry", server=server, tool=tool, attempt=attempt, delay=delay)
                    await asyncio.sleep(delay)
                    continue
                break
            except Exception as e:  # 未分类异常按服务故障处理，不重试
                last_exc = MCPError(server, tool, f"unexpected: {e}")
                self._record_audit(server, tool, params, None, trace_id, session_id, start, attempt, error=f"unexpected: {e}")
                break

        assert last_exc is not None
        raise last_exc

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
        """记录调用审计（结构对齐 complaint_agent_logs.mcp_calls JSONB）。"""
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

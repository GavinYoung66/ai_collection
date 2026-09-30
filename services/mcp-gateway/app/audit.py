"""mcp_calls 调用审计（Batch 4，任务 5.1 / 5.2）。

每次 MCP 入站调用（tools/list、tools/call）记录一条审计 entry，字段与
``complaint_agent_logs.mcp_calls`` JSONB 对齐（同源于 complaint-service
``MCPClient._record_audit``，不另立字段集）：

    server / tool / params（摘要）/ trace_id / session_id / attempt /
    latency_ms / started_at / ended_at / status(success|error) / error

写出三路：
1. 进程内 ``_call_log``（测试/排障快照，append-only）；
2. structlog 事件 ``mcp_call_audit``（error 走 warning，success 走 debug）；
3. ``call_log_sink.emit_call_log("mcp", entry)``（任务 5.2，best-effort：
   sink 失败 SHALL NOT 阻断工具调用）。

重试由 reliability 层内部消化，按调用（非按 attempt）记录最终结果一次；
业务错重试耗尽走容错 fallback 时，可靠性层经 ``set_fallback_error`` 回传
错误描述——调用方仍收到兜底值（图继续判），但审计如实记 status=error。
"""
from __future__ import annotations

import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import structlog

from common.security.audit import log_action

from app.call_log_sink import emit_call_log

logger = structlog.get_logger()

# 进程内审计快照（append-only）
_call_log: list[dict[str, Any]] = []

# 可靠性层 fallback 信号：业务错重试耗尽 → 兜默认值时写入错误描述；
# dispatch 在调用前置空、调用后取走，避免跨调用串扰（同一 asyncio task）。
_fallback_error: ContextVar[Optional[str]] = ContextVar(
    "mcp_gateway_fallback_error", default=None
)


def set_fallback_error(message: str) -> None:
    """可靠性层容错兜底被触发时回传业务错误描述（status=error 审计证据）。"""
    _fallback_error.set(message)


def take_fallback_error() -> Optional[str]:
    """取走本次调用的 fallback 错误描述（读后即清）。"""
    message = _fallback_error.get()
    if message is not None:
        _fallback_error.set(None)
    return message


def reset_fallback_error() -> None:
    """调用前置空，确保只统计本次调用产生的 fallback 信号。"""
    _fallback_error.set(None)


def reset_call_log() -> None:
    """清空进程内审计快照（测试隔离用）。"""
    _call_log.clear()


def get_call_log() -> list[dict[str, Any]]:
    """审计日志快照（浅拷贝，调用方不得反向修改原条目）。"""
    return list(_call_log)


def summarize_params(params: Any) -> Any:
    """参数摘要：dict 保留 key、value 截断 80 字符；非 dict 整体截断。"""
    if isinstance(params, dict):
        return {k: (str(v)[:80] if v is not None else None) for k, v in params.items()}
    return str(params)[:80] if params is not None else None


def record_mcp_call(
    server: str,
    tool: str,
    params: Any,
    *,
    trace_id: str,
    session_id: str,
    start: float,
    error: Optional[str] = None,
    attempt: int = 1,
) -> dict[str, Any]:
    """构造并写出一条 mcp_calls 审计 entry（结构对齐 mcp_calls JSONB）。"""
    latency_ms = int((time.monotonic() - start) * 1000)
    ended_at = datetime.now(timezone.utc)
    started_at = ended_at - timedelta(milliseconds=max(latency_ms, 0))
    entry: dict[str, Any] = {
        "server": server,
        "tool": tool,
        "params": summarize_params(params),
        "trace_id": trace_id,
        "session_id": session_id,
        "attempt": attempt,
        "latency_ms": latency_ms,
        "started_at": started_at,
        "ended_at": ended_at,
        "status": "error" if error else "success",
        "error": error,
    }
    _call_log.append(entry)
    # datetime 经 structlog 渲染序列化（与 complaint-service 同配置）
    if error:
        logger.warning("mcp_call_audit", **entry)
    else:
        logger.debug("mcp_call_audit", **entry)
    # 任务 5.2：经 call_log_sink 以 channel=mcp 写出；best-effort 不阻断主流程
    try:
        emit_call_log("mcp", entry)
    except Exception:  # noqa: BLE001 — 审计写出失败永不阻断业务调用
        logger.warning("mcp_call_audit_emit_failed", server=server, tool=tool)
    return entry


def report_pii_leak(server: str, tool: str, *, trace_id: str = "") -> None:
    """检测到出口明文 PII 时触发 ``mcp_pii_leak_sanitized`` 审计事件（任务 5.4）。

    best-effort：事件上报失败 SHALL NOT 阻断脱敏与工具返回。
    """
    try:
        logger.warning(
            "mcp_pii_leak_detected", server=server, tool=tool, trace_id=trace_id
        )
        log_action(
            action="mcp_pii_leak_sanitized",
            resource=f"{server}/{tool}",
            trace_id=trace_id,
        )
    except Exception:  # noqa: BLE001 — 审计事件失败不阻断主流程
        logger.warning("mcp_pii_leak_event_failed", server=server, tool=tool)

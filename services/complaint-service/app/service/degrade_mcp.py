"""MCP 降级（9.2b）—— 4 场景。

1. 业务工单 MCP 失败 → 证据缺失触发反思循环（A5006）
2. 通话质检 MCP 失败 → 通话证据缺失标注（A5008）
3. 数据中台回写失败 → 重试 3 次+告警不阻塞（A5013）
4. MCP 权限拒绝 → 终止流程+安全审计
"""
from __future__ import annotations

import structlog
from datetime import datetime, timezone
from typing import Any

from common.errors.codes import ErrorCode

logger = structlog.get_logger()


class DegradeMCP:
    @staticmethod
    def _exc(code: str, reason: str) -> dict:
        return {"code": code, "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat()}

    def handle_business_ticket_failure(self, state: dict | None = None) -> dict:
        """业务工单失败 → 证据缺失，触发反思。"""
        out = {
            "evidence_missing": True,
            "need_reflection": True,
            "exception_info": [self._exc(ErrorCode.A5006.value, "业务工单 MCP 失败，证据缺失")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out

    def handle_call_quality_failure(self, state: dict | None = None) -> dict:
        """通话质检失败 → 通话证据缺失标注。"""
        out = {
            "call_evidence_missing": True,
            "exception_info": [self._exc(ErrorCode.A5008.value, "通话质检 MCP 失败")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out

    def handle_writeback_failure(self, retries: int = 3,
                                 state: dict | None = None) -> bool:
        """数据中台回写失败 → 重试 3 次，告警不阻塞。返回是否最终成功。"""
        for _ in range(retries):
            # 本批 stub：始终失败，批次10+ 接真回写
            continue
        exc = self._exc(ErrorCode.A5013.value, f"数据中台回写失败 retries={retries}")
        if state is not None:
            state.setdefault("exception_info", []).append(exc)
        logger.warning("mcp_writeback_failed", retries=retries, code=ErrorCode.A5013.value)
        return False  # 不阻塞主流程

    def handle_permission_denied(self, server: str, tool: str,
                                 trace_id: str = "") -> bool:
        """权限拒绝 → 终止流程 + 安全审计。返回 False 表示终止。"""
        from common.security.audit import log_action
        log_action(action="mcp_permission_denied", resource=f"{server}/{tool}",
                   trace_id=trace_id)
        logger.warning("mcp_permission_denied", server=server, tool=tool)
        return False


# 全局单例
degrade_mcp = DegradeMCP()

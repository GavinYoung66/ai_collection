"""人工复核 SLA 与权限服务（8.2b）。

- SLA 3 级：普通 24h / 紧急 4h / 高风险 2h，超时自动升级
- 权限 4 级 RBAC：一审(level1) / 二审(level2) / 终审(level3) / 只读(readonly)
- AI-人工 diff：verdict 一致率 + response_diff 行数
- 明文 PII 双人授权（复用批次7 audit_service）
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from app.service.audit_service import access_plaintext


class SLALevel(str, Enum):
    normal = "normal"          # 24h
    urgent = "urgent"          # 4h
    high_risk = "high_risk"    # 2h


SLA_HOURS = {
    SLALevel.normal: 24,
    SLALevel.urgent: 4,
    SLALevel.high_risk: 2,
}


class ReviewRole(str, Enum):
    level1 = "level1"      # 一审
    level2 = "level2"      # 二审
    level3 = "level3"      # 终审
    readonly = "readonly"  # 只读


# 可提交动作的角色
_SUBMIT_ROLES = {ReviewRole.level1, ReviewRole.level2, ReviewRole.level3}


class ReviewSLAService:
    def __init__(self) -> None:
        self._sla: dict[str, SLALevel] = {}

    def set_sla(self, complaint_id: str, level: SLALevel) -> None:
        self._sla[complaint_id] = level

    def get_sla_hours(self, complaint_id: str) -> int:
        return SLA_HOURS.get(self._sla.get(complaint_id, SLALevel.normal), 24)

    def check_timeout(self, complaint_id: str, created_ts: float, now_ts: float) -> bool:
        hours = self.get_sla_hours(complaint_id)
        return (now_ts - created_ts) > hours * 3600

    # ── 权限 ──

    def can_submit(self, role: ReviewRole | str) -> bool:
        if isinstance(role, str):
            role = ReviewRole(role)
        return role in _SUBMIT_ROLES

    def can_override(self, role: ReviewRole | str, target_role: ReviewRole | str) -> bool:
        """终审可覆盖二审及以下。"""
        if isinstance(role, str):
            role = ReviewRole(role)
        if isinstance(target_role, str):
            target_role = ReviewRole(target_role)
        order = {ReviewRole.level1: 1, ReviewRole.level2: 2, ReviewRole.level3: 3}
        return order.get(role, 0) > order.get(target_role, 0)

    # ── AI-人工 diff ──

    def compute_ai_human_diff(self, ai_verdict: str, human_verdict: str,
                              ai_resp: str = "", human_resp: str = "") -> dict:
        verdict_match_rate = 1.0 if ai_verdict == human_verdict else 0.0
        ai_lines = (ai_resp or "").splitlines()
        human_lines = (human_resp or "").splitlines()
        # 简易 diff 行数：不同行的最大集合大小
        diff_lines = len(set(ai_lines) ^ set(human_lines))
        return {
            "verdict_match_rate": verdict_match_rate,
            "response_diff_lines": diff_lines,
            "ai_verdict": ai_verdict,
            "human_verdict": human_verdict,
        }

    # ── 明文 PII 双人授权（对齐 10.3） ──

    def request_plaintext(self, role: str, resource: str = "",
                          trace_id: str = "", dual_approved: bool = False) -> bool:
        return access_plaintext(role, resource, trace_id=trace_id,
                                dual_approved=dual_approved)


# 全局单例
review_sla_service = ReviewSLAService()

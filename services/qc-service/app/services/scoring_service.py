"""扣分制打分。主口径 deduction_total；库列 deduction_total / risk_level；禁止用分数/扣分切 risk_level。

penalty 为 severity→扣分值的唯一事实源，由调用方从 active 规则包（qc_rules.penalty）注入；
本模块不持有任何硬编码分值常量。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from app.services.ac_filter import WordHit


@dataclass(frozen=True)
class ScoreResult:
    deduction_total: int
    risk_level: str
    qc_result: str
    qc_status: str
    high_count: int
    med_count: int
    low_count: int

    @property
    def compliance_score(self) -> int:
        """派生兼容列：compliance_score = max(0, 100 - deduction_total)。"""
        return max(0, 100 - self.deduction_total)


class ScoringService:
    def score(self, hits: Sequence[WordHit], penalty: Mapping[str, int]) -> ScoreResult:
        deduction = 0
        high = med = low = 0
        agent_high = False
        for hit in hits:
            if hit.speaker == "agent" and hit.severity == "high":
                agent_high = True
            if not hit.score_eligible:
                continue
            deduction += int(penalty.get(hit.severity, 0))
            if hit.severity == "high":
                high += 1
            elif hit.severity == "medium":
                med += 1
            else:
                low += 1
        if agent_high:
            risk = "high"
        elif deduction >= 20:
            risk = "medium"
        else:
            risk = "low"
        if deduction > 0:
            qc_result, qc_status = "pending_review", "PENDING_REVIEW"
        else:
            qc_result, qc_status = "compliant", "DONE"
        return ScoreResult(
            deduction_total=deduction,
            risk_level=risk,
            qc_result=qc_result,
            qc_status=qc_status,
            high_count=high,
            med_count=med,
            low_count=low,
        )

    def to_http(self, result: ScoreResult) -> dict[str, object]:
        """HTTP 键：deduction_total 主口径 + compliance_score 派生；grade 恒 null。"""
        return {
            "deduction_total": result.deduction_total,
            "compliance_score": result.compliance_score,
            "risk": result.risk_level,
            "grade": None,
        }

    def to_event(self, result: ScoreResult) -> dict[str, object]:
        """事件键：deduction 主口径 + score 派生；grade 恒 null。"""
        return {
            "deduction": result.deduction_total,
            "score": result.compliance_score,
            "risk": result.risk_level,
            "grade": None,
        }

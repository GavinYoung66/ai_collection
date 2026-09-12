"""100 分制扣分。库列 compliance_score / risk_level；禁止用分数切 risk_level。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from app.services.ac_filter import WordHit

PENALTY = {"high": 20, "medium": 8, "low": 2}


@dataclass(frozen=True)
class ScoreResult:
    compliance_score: int
    risk_level: str
    grade: str
    qc_result: str
    qc_status: str
    high_count: int
    med_count: int
    low_count: int
    penalty_total: int


def grade_for(score: int) -> str:
    if score >= 90:
        return "EXCELLENT"
    if score >= 80:
        return "PASS"
    if score >= 60:
        return "WARNING"
    return "SERIOUS"


class ScoringService:
    def score(self, hits: Sequence[WordHit]) -> ScoreResult:
        penalty = 0
        high = med = low = 0
        agent_high = False
        for hit in hits:
            if hit.speaker == "agent" and hit.severity == "high":
                agent_high = True
            if not hit.score_eligible:
                continue
            penalty += PENALTY.get(hit.severity, 0)
            if hit.severity == "high":
                high += 1
            elif hit.severity == "medium":
                med += 1
            else:
                low += 1
        compliance = max(0, 100 - penalty)
        if agent_high:
            risk = "high"
        elif penalty >= 20:
            risk = "medium"
        else:
            risk = "low"
        if penalty > 0:
            qc_result, qc_status = "pending_review", "PENDING_REVIEW"
        else:
            qc_result, qc_status = "compliant", "DONE"
        return ScoreResult(
            compliance_score=compliance,
            risk_level=risk,
            grade=grade_for(compliance),
            qc_result=qc_result,
            qc_status=qc_status,
            high_count=high,
            med_count=med,
            low_count=low,
            penalty_total=penalty,
        )

    def to_http(self, result: ScoreResult) -> dict[str, object]:
        return {
            "compliance_score": result.compliance_score,
            "risk": result.risk_level,
            "grade": result.grade,
        }

    def to_event(self, result: ScoreResult) -> dict[str, object]:
        return {
            "score": result.compliance_score,
            "risk": result.risk_level,
            "grade": result.grade,
        }

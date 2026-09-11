"""合并后 R10：该转未转。禁止读护栏日志。"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

from app.services.ac_filter import WordHit

THREAT_ONCE = frozenset({"threat", "fake_identity"})
THREAT_TWICE = frozenset({"abuse", "induce"})


def _result_missing(result: Any) -> bool:
    return result is None or result == ""


def _should_transfer(merged: Sequence[WordHit]) -> bool:
    once = 0
    twice = 0
    for hit in merged:
        if hit.speaker != "agent":
            continue
        tag = hit.redline_tag or ""
        if tag in THREAT_ONCE:
            once += 1
        if tag in THREAT_TWICE:
            twice += 1
    return once >= 1 or twice >= 2


def evaluate_r10(merged: Sequence[WordHit], result: Any) -> tuple[WordHit | None, bool]:
    """返回 (hit, skipped)。skipped=True 时调用方把 R10 记入 skipped_rules。"""
    if _result_missing(result):
        return None, True
    if result == "escalated":
        return None, False
    if not _should_transfer(merged):
        return None, False
    hit = WordHit(
        speaker="agent",
        word="R10",
        severity="high",
        score_eligible=True,
        source="rule",
        redline_tag="unverified",
        rule_id="R10",
        evidence_text="应转未转",
    )
    return hit, False


@dataclass
class R10Decision:
    hit: WordHit | None
    skipped: bool


class PostMergeR10:
    def apply(self, merged: Sequence[WordHit], result: Any) -> R10Decision:
        hit, skipped = evaluate_r10(merged, result)
        return R10Decision(hit=hit, skipped=skipped)

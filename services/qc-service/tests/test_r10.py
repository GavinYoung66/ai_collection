"""QC-4.7：R10 必须在 MergeHits 之后；缺 result skip 且不填 unverified。"""
from __future__ import annotations

from app.services.ac_filter import WordHit
from app.services.alert_service import AlertService
from app.services.merge_hits import merge_hits
from app.services.r10 import evaluate_r10, PostMergeR10
from app.services.scoring_service import ScoringService


def _agent_threat() -> WordHit:
    return WordHit(
        speaker="agent",
        word="威胁",
        severity="high",
        score_eligible=True,
        source="ac",
        redline_tag="threat",
        turn_id="t1",
        word_id="w-threat",
    )


class TestPostMergeR10:
    def test_missing_result_skips_without_unverified_row(self):
        merged = merge_hits([_agent_threat()], [], [])
        hit, skipped = evaluate_r10(merged, None)
        assert skipped is True
        assert hit is None
        assert not any(item.redline_tag == "unverified" for item in merged)
        scored = ScoringService().score(merged)
        assert scored.compliance_score == 80
        alerts = AlertService().on_high_risk(merged)
        assert len(alerts) == 1
        assert alerts[0].get("rule_id") != "R10"

    def test_completed_with_agent_threat_appends_one_r10(self):
        merged = merge_hits([_agent_threat()], [], [])
        hit, skipped = evaluate_r10(merged, "completed")
        assert skipped is False
        assert hit is not None
        assert hit.rule_id == "R10"
        assert hit.redline_tag == "unverified"
        assert hit.speaker == "agent"
        assert hit.severity == "high"
        with_r10 = [*merged, hit]
        assert sum(1 for item in with_r10 if item.rule_id == "R10") == 1

    def test_escalated_is_non_hit_not_skip(self):
        merged = merge_hits([_agent_threat()], [], [])
        hit, skipped = evaluate_r10(merged, "escalated")
        assert skipped is False
        assert hit is None

    def test_abuse_induce_needs_two(self):
        abuse = WordHit(
            speaker="agent",
            word="骂",
            severity="medium",
            score_eligible=True,
            source="ac",
            redline_tag="abuse",
            turn_id="t1",
        )
        hit, skipped = evaluate_r10([abuse], "completed")
        assert hit is None
        assert skipped is False
        induce = WordHit(
            speaker="agent",
            word="诱导",
            severity="low",
            score_eligible=True,
            source="ac",
            redline_tag="induce",
            turn_id="t2",
        )
        hit, skipped = evaluate_r10([abuse, induce], "completed")
        assert hit is not None
        assert skipped is False

    def test_does_not_read_guardrail_pass(self):
        import inspect

        source = inspect.getsource(PostMergeR10) + inspect.getsource(evaluate_r10)
        assert "guardrail.pass" not in source
        assert "guardrail" not in source

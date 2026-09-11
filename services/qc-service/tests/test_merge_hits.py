"""QC-4.6：MergeHits 去重、跨层同 tag、告警只看合并后。"""
from __future__ import annotations

from app.services.ac_filter import WordHit
from app.services.alert_service import AlertService
from app.services.merge_hits import merge_hits
from app.services.scoring_service import ScoringService


def _hit(**kwargs) -> WordHit:
    defaults = {
        "speaker": "agent",
        "word": "x",
        "severity": "high",
        "score_eligible": True,
        "source": "ac",
        "turn_id": "t1",
    }
    defaults.update(kwargs)
    return WordHit(**defaults)  # type: ignore[arg-type]


class TestMergeHits:
    def test_same_turn_ac_threat_and_r11_deducts_20_not_28(self):
        ac = _hit(
            word="威胁",
            source="ac",
            severity="high",
            redline_tag="threat",
            word_id="w-threat",
            normalized_word="威胁",
        )
        r11 = _hit(
            word="R11",
            source="rule",
            severity="medium",
            redline_tag="threat",
            rule_id="R11",
            score_eligible=True,
        )
        merged = merge_hits([ac], [r11], [])
        assert len(merged) == 1
        assert merged[0].severity == "high"
        assert merged[0].rule_id == "R11"
        scored = ScoringService().score(merged)
        assert scored.compliance_score == 80
        assert scored.penalty_total == 20
        assert scored.high_count == 1
        alerts = AlertService().on_high_risk(merged)
        assert len(alerts) == 1
        assert alerts[0]["level"] == "high"

    def test_user_hit_not_score_eligible(self):
        user = _hit(speaker="user", severity="high", score_eligible=True, source="ac")
        merged = merge_hits([user], [], [])
        assert merged[0].score_eligible is False
        scored = ScoringService().score(merged)
        assert scored.compliance_score == 100
        assert scored.risk_level == "low"
        assert AlertService().on_high_risk(merged) == []

    def test_drops_speaker_outside_agent_user(self):
        other = _hit()
        other.speaker = "system"  # type: ignore[assignment]
        merged = merge_hits([other], [], [])
        assert merged == []

    def test_drops_llm_rule_id_outside_r01_r15(self):
        bad = _hit(source="llm", rule_id="R99", severity="high")
        good = _hit(source="llm", rule_id="R12", severity="high", turn_id="t2")
        merged = merge_hits([], [], [bad, good])
        assert len(merged) == 1
        assert merged[0].rule_id == "R12"

    def test_r05_low_and_unmerged_dup_must_not_alert(self):
        r05 = _hit(
            source="rule",
            severity="low",
            rule_id="R05",
            redline_tag="threat",
            score_eligible=True,
        )
        dup_a = _hit(word="威胁", word_id="w1", source="ac", redline_tag="threat")
        dup_b = _hit(word="威胁", word_id="w1", source="ac", redline_tag="threat")
        merged_dups = merge_hits([dup_a, dup_b], [], [])
        assert len(merged_dups) == 1
        assert len(AlertService().on_high_risk(merged_dups)) == 1
        only_r05 = merge_hits([], [r05], [])
        assert len(only_r05) == 1
        assert only_r05[0].severity == "low"
        assert AlertService().on_high_risk(only_r05) == []
        scored = ScoringService().score(only_r05)
        assert scored.compliance_score == 98
        assert scored.risk_level == "low"
        assert scored.qc_result == "violation"

"""evidence_helper 单元测试：展平 + 冲突解决 6 条规则。"""
from __future__ import annotations

from app.utils.evidence_helper import (
    _collect_all_evidence,
    apply_corruption_degradation,
    detect_core_conflict,
    filter_by_confidence,
    mark_conflict_pending,
    mark_medium_auxiliary,
    sort_by_priority,
)


def _ev(eid: str, source: str, content: str = "", **kw) -> dict:
    base = {
        "evidence_id": eid,
        "source": source,
        "content": content,
        "confidence": 1.0,
        "confidence_level": "high",
        "is_corrupted": False,
    }
    base.update(kw)
    return base


# ── 规则1：sort_by_priority ──
class TestSortByPriority:
    def test_order(self):
        items = [
            _ev("u", "user_statement"),
            _ev("c", "call"),
            _ev("s", "structured_data"),
            _ev("h", "history_complaint"),
            _ev("q", "qc"),
            _ev("t", "history_ticket"),
        ]
        ordered = [e["evidence_id"] for e in sort_by_priority(items)]
        assert ordered == ["s", "c", "q", "t", "h", "u"]


# ── 规则2：filter_by_confidence ──
class TestFilterByConfidence:
    def test_filters_low(self):
        items = [
            _ev("h", "call", confidence_level="high"),
            _ev("m", "call", confidence_level="medium"),
            _ev("l", "call", confidence_level="low"),
        ]
        kept = filter_by_confidence(items, min_level="medium")
        ids = {e["evidence_id"] for e in kept}
        assert ids == {"h", "m"}  # low 被滤除


# ── 规则3：mark_medium_auxiliary ──
class TestMarkMediumAuxiliary:
    def test_medium_marked_auxiliary(self):
        items = [_ev("m", "call", confidence_level="medium")]
        mark_medium_auxiliary(items)
        assert items[0].get("auxiliary") is True

    def test_high_not_marked(self):
        items = [_ev("h", "call", confidence_level="high")]
        mark_medium_auxiliary(items)
        assert "auxiliary" not in items[0]


# ── 规则4：detect_core_conflict ──
class TestDetectCoreConflict:
    def test_amount_conflict(self):
        items = [
            _ev("a", "call", content="逾期金额 5000 元", case_id="c1"),
            _ev("b", "structured_data", content="逾期金额 3000 元", case_id="c1"),
        ]
        conflicts = detect_core_conflict(items)
        assert len(conflicts) == 2
        assert all(e.get("conflict_highlight") for e in conflicts)

    def test_no_conflict_same_number(self):
        items = [
            _ev("a", "call", content="逾期 5000 元", case_id="c1"),
            _ev("b", "qc", content="金额 5000 元", case_id="c1"),
        ]
        assert detect_core_conflict(items) == []


# ── 规则5：apply_corruption_degradation ──
class TestApplyCorruptionDegradation:
    def test_corrupted_becomes_medium(self):
        items = [_ev("x", "call", content="部分转写", is_corrupted=True, confidence=0.9, confidence_level="high")]
        result = apply_corruption_degradation(items)
        assert result[0]["confidence_level"] == "medium"
        assert result[0]["confidence"] == 0.3
        assert result[0]["corrupted_degraded"] is True

    def test_core_missing_becomes_low(self):
        items = [_ev("y", "call", content="", is_corrupted=True, core_content_missing=True, confidence_level="high")]
        result = apply_corruption_degradation(items)
        assert result[0]["confidence_level"] == "low"
        assert result[0]["confidence"] == 0.0

    def test_non_corrupted_unchanged(self):
        items = [_ev("x", "call", content="完整转写", is_corrupted=False, confidence_level="high")]
        apply_corruption_degradation(items)
        assert items[0]["confidence_level"] == "high"


# ── 规则6：mark_conflict_pending ──
class TestMarkConflictPending:
    def test_conflict_marked_pending(self):
        items = [
            _ev("a", "call", conflict_highlight=True),
            _ev("b", "call", conflict_highlight=False),
        ]
        mark_conflict_pending(items)
        assert items[0]["pending_for_package"] is True
        assert "pending_for_package" not in items[1]


# ── _collect_all_evidence ──
class TestCollectAllEvidence:
    def test_flatten_all_sources(self):
        state = {
            "case_evidence": {
                "c1": {
                    "call_evidence": [_ev("call1", "call")],
                    "history_tickets": [_ev("tk1", "history_ticket")],
                    "history_complaints": [_ev("cp1", "history_complaint")],
                    "qc_alerts": [_ev("qc1", "qc")],
                }
            },
            "cross_case_evidence": [_ev("cross1", "structured_data")],
            "historical_complaint_evidence": [_ev("hist1", "history_complaint")],
        }
        items = _collect_all_evidence(state)
        ids = {e["evidence_id"] for e in items}
        assert ids == {"call1", "tk1", "cp1", "qc1", "cross1", "hist1"}
        # case_evidence 中的证据应自动带 case_id
        call_ev = next(e for e in items if e["evidence_id"] == "call1")
        assert call_ev["case_id"] == "c1"

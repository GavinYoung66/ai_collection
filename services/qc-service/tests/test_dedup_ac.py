"""QC-4.2：DedupACWindow 只作用于 AC；三级精度不混用。"""
from __future__ import annotations

import pytest

from app.services.ac_filter import ACFilter, SensitiveWord, WordHit
from app.services.dedup_ac import DedupACWindow

_PENALTY = {"high": 20, "medium": 8, "low": 2}


def _preview_score(hits: list[WordHit]) -> int:
    score = 100
    for hit in hits:
        if not hit.score_eligible:
            continue
        score -= _PENALTY[hit.severity]
    return max(score, 0)


@pytest.fixture
async def ac_filter() -> ACFilter:
    filt = ACFilter()
    await filt.reload([SensitiveWord(word="可以减免", category="induce", word_id="w_induce")])
    return filt


def _ac_hit(**kwargs) -> WordHit:
    base = dict(
        speaker="agent",
        word="可以减免",
        severity="low",
        score_eligible=True,
        source="ac",
        word_id="w_induce",
        normalized_word="可以减免",
        redline_tag="induce",
    )
    base.update(kwargs)
    return WordHit(**base)  # type: ignore[arg-type]


class TestDedupACWindow:
    def test_default_window_is_30000(self):
        window = DedupACWindow()
        assert window.same_word_window_ms == 30000
        assert window.window_ms == 30000

    def test_explicit_same_word_window_ms(self):
        window = DedupACWindow(same_word_window_ms=10000)
        assert window.window_ms == 10000
        assert window.same_word_window_ms == 10000

    def test_same_word_window_ms_wins_over_window_ms(self):
        window = DedupACWindow(window_ms=20000, same_word_window_ms=10000)
        assert window.window_ms == 10000

    @pytest.mark.asyncio
    async def test_same_word_within_20s_drops_second(self, ac_filter: ACFilter):
        turns = [
            {
                "turn_id": "t1",
                "speaker": "agent",
                "text": "可以给你可以减免",
                "turn_index": 1,
                "hit_ts": "2026-09-09T10:00:00+08:00",
            },
            {
                "turn_id": "t2",
                "speaker": "agent",
                "text": "再次可以减免",
                "turn_index": 2,
                "hit_ts": "2026-09-09T10:00:20+08:00",
            },
        ]
        raw = ac_filter.match_turns(turns)
        assert len(raw) >= 2
        kept = DedupACWindow(30000).apply(raw)
        eligible = [hit for hit in kept if hit.score_eligible]
        assert len(eligible) == 1
        assert eligible[0].severity == "low"
        assert _preview_score(kept) == 98

    def test_turn_index_fallback_drops_second(self):
        hits = [
            _ac_hit(turn_id="t1", turn_index=1),
            _ac_hit(turn_id="t2", turn_index=2),
        ]
        kept = DedupACWindow(30000).apply(hits)
        assert len(kept) == 1
        assert kept[0].turn_id == "t1"

    def test_offset_window_without_hit_ts(self):
        hits = [
            _ac_hit(turn_id="t1", turn_index=1, start_offset_ms=1000),
            _ac_hit(turn_id="t2", turn_index=9, start_offset_ms=5000),
        ]
        kept = DedupACWindow(30000).apply(hits)
        assert len(kept) == 1

    def test_does_not_mix_hit_ts_with_turn_index(self):
        hits = [
            _ac_hit(
                turn_id="t1",
                turn_index=1,
                hit_ts="2026-09-09T10:00:00+08:00",
            ),
            _ac_hit(
                turn_id="t2",
                turn_index=2,
                hit_ts="2026-09-09T10:01:00+08:00",
            ),
        ]
        kept = DedupACWindow(30000).apply(hits)
        assert len(kept) == 2

    def test_rule_shaped_hits_pass_through(self):
        ac = [
            _ac_hit(turn_id="t1", turn_index=1, hit_ts="2026-09-09T10:00:00+08:00"),
            _ac_hit(turn_id="t2", turn_index=2, hit_ts="2026-09-09T10:00:20+08:00"),
        ]
        rule = WordHit(
            speaker="agent",
            word="可以减免",
            severity="low",
            score_eligible=True,
            source="rule",
            word_id="w_induce",
            normalized_word="可以减免",
            redline_tag="threat",
            turn_id="t2",
            turn_index=2,
            hit_ts="2026-09-09T10:00:20+08:00",
            rule_id="R05",
        )
        kept = DedupACWindow(30000).apply([*ac, rule])
        rule_kept = [hit for hit in kept if hit.rule_id == "R05"]
        ac_kept = [hit for hit in kept if hit.source == "ac"]
        assert len(rule_kept) == 1
        assert len(ac_kept) == 1

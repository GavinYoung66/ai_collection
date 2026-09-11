"""QC-4.1：ACFilter 仅 agent 计分；ai→agent；不读护栏日志。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.integrations.guardrails_adapter import classify_category
from app.services.ac_filter import ACFilter, SensitiveWord
from app.services.scoring_service import __doc__ as scoring_doc


_APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture
async def ac_filter() -> ACFilter:
    filt = ACFilter()
    await filt.reload(
        [
            SensitiveWord(word="自杀", category="threat", word_id="w_suicide"),
            SensitiveWord(word="投诉", category="abuse", word_id="w_complaint"),
            SensitiveWord(word="不要再打", category="induce", word_id="w_stop"),
            SensitiveWord(word="可以减免", category="induce", word_id="w_induce"),
            SensitiveWord(word="蠢货", category="abuse", word_id="w_abuse"),
            SensitiveWord(word="强制执行", category="threat", word_id="w_threat"),
        ]
    )
    return filt


class TestClassify:
    def test_three_way_map(self):
        assert classify_category("threat") == ("high", "threat")
        assert classify_category("abuse") == ("medium", "abuse")
        assert classify_category("induce") == ("low", "induce")


class TestACFilterSpeaker:
    @pytest.mark.asyncio
    async def test_user_row_not_score_eligible(self, ac_filter: ACFilter):
        suicide = ac_filter.match("我想自杀了", "user")
        complaint = ac_filter.match("我要去投诉你们", "user")
        stop = ac_filter.match("不要再打了", "user")
        for hits in (suicide, complaint, stop):
            assert hits
            assert all(hit.speaker == "user" for hit in hits)
            assert all(hit.score_eligible is False for hit in hits)

    @pytest.mark.asyncio
    async def test_user_original_not_recorded_as_agent(self, ac_filter: ACFilter):
        hits = ac_filter.match("我想自杀了", "user")
        assert hits
        assert not any(hit.speaker == "agent" for hit in hits)
        assert not any(hit.score_eligible for hit in hits)

    @pytest.mark.asyncio
    async def test_ai_maps_to_agent_and_is_eligible(self, ac_filter: ACFilter):
        hits = ac_filter.match("我们可以给你可以减免", "ai")
        assert hits
        assert all(hit.speaker == "agent" for hit in hits)
        assert all(hit.score_eligible is True for hit in hits)
        assert hits[0].severity == "low"

    @pytest.mark.asyncio
    async def test_human_maps_to_agent(self, ac_filter: ACFilter):
        hits = ac_filter.match("你这个蠢货", "human")
        assert hits
        assert hits[0].speaker == "agent"
        assert hits[0].score_eligible is True
        assert hits[0].severity == "medium"

    @pytest.mark.asyncio
    async def test_agent_threat_is_high(self, ac_filter: ACFilter):
        hits = ac_filter.match("不还就强制执行", "agent")
        assert hits
        assert hits[0].severity == "high"
        assert hits[0].redline_tag == "threat"
        assert hits[0].score_eligible is True


class TestACFilterMatcherErrors:
    def test_empty_text_returns_empty(self):
        hits = ACFilter().match("", "agent")
        assert hits == []

    def test_automaton_not_ready_returns_empty(self, caplog):
        filt = ACFilter()
        with caplog.at_level("WARNING"):
            hits = filt.match("强制执行", "agent")
        assert hits == []
        assert not any(getattr(hit, "score_eligible", False) for hit in hits)
        assert any("automaton not ready" in rec.message for rec in caplog.records)

    def test_unexpected_runtime_error_propagates(self):
        filt = ACFilter()

        class _Boom:
            def match(self, text: str):
                raise RuntimeError("matcher boom")

        filt._matcher = _Boom()  # type: ignore[assignment]
        with pytest.raises(RuntimeError, match="matcher boom"):
            filt.match("强制执行", "agent")

    def test_unrelated_attribute_error_propagates(self):
        filt = ACFilter()

        class _Boom:
            def match(self, text: str):
                raise AttributeError("unrelated missing attr")

        filt._matcher = _Boom()  # type: ignore[assignment]
        with pytest.raises(AttributeError, match="unrelated missing attr"):
            filt.match("强制执行", "agent")


class TestAdapterDoesNotReadGuardrailLogs:
    def test_no_guardrail_pass_in_ac_stack(self):
        files = [
            _APP / "integrations" / "guardrails_adapter.py",
            _APP / "services" / "ac_filter.py",
            _APP / "services" / "dedup_ac.py",
        ]
        blob = "\n".join(path.read_text(encoding="utf-8") for path in files)
        assert "guardrail.pass" not in blob
        assert "audit_logs" not in blob
        assert scoring_doc  # scoring still a stub module

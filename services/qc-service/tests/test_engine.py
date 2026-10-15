"""QC-11.1：引擎单测覆盖断言 6/19/20/22–25/27；MOCK_LLM=1。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from app.core.config import qc_settings
from app.integrations.rag_client import RagClient
from app.seeds.qc_rules_v1 import PENALTY_V1, V1_SEED
from app.services.ac_filter import ACFilter, SensitiveWord, WordHit
from app.services.alert_service import AlertService
from app.services.dedup_ac import DedupACWindow
from app.services.llm_reviewer import LLMReviewer
from app.services.merge_hits import merge_hits
from app.services.offline_qc_service import OfflineQcService, suspected_eligible
from app.services.r10 import evaluate_r10
from app.services.rule_engine import RuleEngine
from app.services.scoring_service import ScoringService

_APP = Path(__file__).resolve().parents[1] / "app"


@pytest.fixture(autouse=True)
def _mock_llm_env(monkeypatch):
    monkeypatch.setenv("MOCK_LLM", "1")
    monkeypatch.setenv("QC_MOCK_LLM", "true")
    monkeypatch.setattr("app.core.config.qc_settings.QC_MOCK_LLM", True)
    monkeypatch.setattr("app.services.llm_reviewer.qc_settings.QC_MOCK_LLM", True)


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


def _turns(*rows: tuple[str, str, int]) -> list[dict]:
    return [
        {"turn_id": f"t{index}", "speaker": speaker, "text": text, "turn_index": index}
        for speaker, text, index in rows
    ]


def _session(**kwargs) -> dict:
    base = {
        "session_id": "sess_engine",
        "call_ended": True,
        "text_ready": True,
        "turns": _turns(("agent", "您好", 1)),
        "result": None,
        "debtor_name": "张三",
    }
    base.update(kwargs)
    return base


def _r14_pack(*, keywords: list[str]) -> dict:
    pack = deepcopy(V1_SEED)
    for item in pack["rules"]:
        if item["rule_id"] == "R14":
            item["params"]["keywords"] = list(keywords)
    return pack


async def _empty_llm(_messages):
    return "[]"


def _offline(*, rules=None) -> OfflineQcService:
    return OfflineQcService(
        llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0),
        rules=rules or RuleEngine(),
    )


class TestAssertion6:
    def test_same_word_within_20s_dedup_score_98(self):
        hits = [
            _hit(
                word="可以减免",
                severity="low",
                source="ac",
                word_id="w_induce",
                normalized_word="可以减免",
                turn_id="t1",
                turn_index=1,
                hit_ts="2026-09-09T10:00:00+08:00",
            ),
            _hit(
                word="可以减免",
                severity="low",
                source="ac",
                word_id="w_induce",
                normalized_word="可以减免",
                turn_id="t2",
                turn_index=2,
                hit_ts="2026-09-09T10:00:20+08:00",
            ),
        ]
        kept = DedupACWindow(30000).apply(hits)
        scored = ScoringService().score(kept, PENALTY_V1)
        assert len([h for h in kept if h.score_eligible]) == 1
        assert scored.deduction_total == 2
        assert scored.compliance_score == 98


class TestAssertion19:
    def test_same_turn_ac_threat_and_r11_deducts_20_one_alert(self):
        ac = _hit(word="威胁", source="ac", severity="high", redline_tag="threat", word_id="w-threat", normalized_word="威胁")
        r11 = _hit(word="R11", source="rule", severity="medium", redline_tag="threat", rule_id="R11")
        merged = merge_hits([ac], [r11], [])
        scored = ScoringService().score(merged, PENALTY_V1)
        alerts = AlertService().on_high_risk(merged)
        assert len(merged) == 1
        assert scored.deduction_total == 20
        assert scored.compliance_score == 80
        assert len(alerts) == 1
        assert alerts[0]["level"] == "high"


class TestAssertion20:
    def test_no_timestamp_turn_index_diff_1_drops_second_ac(self):
        hits = [
            _hit(
                word="可以减免",
                severity="low",
                source="ac",
                word_id="w1",
                normalized_word="可以减免",
                turn_id="t1",
                turn_index=1,
            ),
            _hit(
                word="可以减免",
                severity="low",
                source="ac",
                word_id="w1",
                normalized_word="可以减免",
                turn_id="t2",
                turn_index=2,
            ),
        ]
        kept = DedupACWindow(30000).apply(hits)
        assert len(kept) == 1
        assert kept[0].turn_id == "t1"
        assert hits[0].hit_ts is None
        assert hits[0].start_offset_ms is None


class TestAssertion22:
    def test_r05_only_deducts_2_no_alert_no_suspected(self):
        r05 = _hit(
            source="rule",
            severity="low",
            rule_id="R05",
            redline_tag="threat",
            score_eligible=True,
        )
        scored = ScoringService().score([r05], PENALTY_V1)
        alerts = AlertService().on_high_risk([r05])
        assert scored.deduction_total == 2
        assert scored.qc_result == "pending_review"
        assert scored.qc_status == "PENDING_REVIEW"
        assert alerts == []
        assert suspected_eligible([r05]) is False


class TestAssertion23:
    @pytest.mark.asyncio
    async def test_r14_skip_without_search_not_partial(self, monkeypatch):
        monkeypatch.setattr(qc_settings, "QC_R14_RAG_ENABLED", False)
        rag = RagClient()
        src = (_APP / "services" / "rule_engine.py").read_text(encoding="utf-8")
        assert "/kb/search" not in src
        engine = RuleEngine(_r14_pack(keywords=[]))
        svc = _offline(rules=engine)
        svc.rag = rag
        result = await svc.run(_session(turns=_turns(("agent", "请尽快处理账单", 1))))
        assert "R14" in result.skipped_rules
        assert not any(hit.rule_id == "R14" for hit in result.hits)
        assert result.qc_status != "PARTIAL"
        assert result.qc_status == "PENDING_REVIEW"
        assert all("/kb/search" not in url for url in rag.recorded_urls())


class TestAssertion24:
    def test_r14_keyword_hits_without_rag_http(self, monkeypatch):
        monkeypatch.setattr(qc_settings, "QC_R14_RAG_ENABLED", False)
        result = RuleEngine().run(_session(turns=_turns(("agent", "再不还就起诉你", 1))))
        r14 = [hit for hit in result.hits if hit.rule_id == "R14"]
        assert r14
        src = (_APP / "services" / "rule_engine.py").read_text(encoding="utf-8")
        assert "/kb/search" not in src
        assert "RagClient" not in src


class TestAssertion25:
    def test_completed_plus_threat_hits_r10_missing_result_skips(self):
        threat = _hit(redline_tag="threat", rule_id="R11", severity="high")
        hit, skipped = evaluate_r10([threat], "completed")
        assert skipped is False
        assert hit is not None
        assert hit.rule_id == "R10"
        assert hit.redline_tag == "unverified"
        miss, miss_skip = evaluate_r10([threat], None)
        assert miss_skip is True
        assert miss is None
        svc_skip = RuleEngine().run(_session(result=None, turns=_turns(("agent", "您好", 1))))
        assert "R10" not in [h.rule_id for h in svc_skip.hits]


class TestAssertion25Offline:
    @pytest.mark.asyncio
    async def test_no_result_skips_r10_without_unverified(self):
        svc = _offline()
        result = await svc.run(_session(result=None, turns=_turns(("agent", "您好请尽快处理", 1))))
        assert "R10" in result.skipped_rules
        assert not any(hit.rule_id == "R10" for hit in result.hits)
        assert not any(hit.redline_tag == "unverified" for hit in result.hits)


class TestAssertion27:
    def test_r04_disclosure_before_confirm_then_shiwo_clears(self):
        before = RuleEngine().run(
            _session(turns=_turns(("agent", "你欠款逾期了", 1), ("user", "啊？", 2)))
        )
        assert any(hit.rule_id == "R04" for hit in before.hits)
        after = RuleEngine().run(
            _session(
                turns=_turns(
                    ("agent", "请问是张先生吗", 1),
                    ("user", "是我", 2),
                    ("agent", "你欠款逾期了要还", 3),
                )
            )
        )
        assert not any(hit.rule_id == "R04" for hit in after.hits)


class TestUserNotScored:
    @pytest.mark.asyncio
    async def test_user_complaint_suicide_stop_not_scored(self):
        ac = ACFilter()
        await ac.reload(
            [
                SensitiveWord(word="自杀", category="threat"),
                SensitiveWord(word="投诉", category="abuse"),
                SensitiveWord(word="不要再打", category="induce"),
            ]
        )
        suicide = ac.match("我想自杀了", "user")
        complaint = ac.match("我要去投诉", "user")
        stop = ac.match("不要再打了", "user")
        for hits in (suicide, complaint, stop):
            assert hits
            assert all(hit.score_eligible is False for hit in hits)
        scored = ScoringService().score([*suicide, *complaint, *stop], PENALTY_V1)
        assert scored.deduction_total == 0
        assert scored.compliance_score == 100

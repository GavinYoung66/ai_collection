"""QC-4.8：OfflineQcService 顺序、打分、WAIT_TEXT、A2001/A2003。"""
from __future__ import annotations

from app.core.config import qc_settings
from app.repositories.job_store import QcJobStore, job_key
from app.services.ac_filter import WordHit
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import OfflineQcService, suspected_eligible
from app.services.replay_service import ReplayService
from app.services.scoring_service import ScoringService
from common.errors.codes import ERROR_MESSAGES
from tests.test_job_store import MemoryRedis


def _turns(*rows: tuple[str, str, int]) -> list[dict]:
    return [
        {
            "turn_id": f"t{index}",
            "speaker": speaker,
            "text": text,
            "turn_index": index,
        }
        for speaker, text, index in rows
    ]


def _ready_session(**kwargs) -> dict:
    base = {
        "session_id": "sess_offline",
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "turns": _turns(("agent", "让你妈妈接电话", 1)),
        "result": None,
    }
    base.update(kwargs)
    return base


class BoomAC:
    def match_turns(self, turns):
        raise RuntimeError("boom")


async def _empty_llm(_messages):
    return "[]"


class TestScoringIndependent:
    def test_single_agent_high_is_80_high_pass_pending_review(self):
        hit = WordHit(
            speaker="agent",
            word="R01",
            severity="high",
            score_eligible=True,
            source="rule",
            rule_id="R01",
            redline_tag="privacy_leak",
        )
        scored = ScoringService().score([hit])
        assert scored.compliance_score == 80
        assert scored.risk_level == "high"
        assert scored.grade == "PASS"
        assert scored.qc_result == "pending_review"
        assert scored.qc_status == "PENDING_REVIEW"
        http = ScoringService().to_http(scored)
        event = ScoringService().to_event(scored)
        assert http["compliance_score"] == 80
        assert http["risk"] == "high"
        assert "risk_level" not in http
        assert event["score"] == 80
        assert event["risk"] == "high"
        assert "compliance_score" not in event

    def test_risk_not_from_score_bands(self):
        med = WordHit(
            speaker="agent",
            word="R11",
            severity="medium",
            score_eligible=True,
            source="rule",
            rule_id="R11",
        )
        scored = ScoringService().score([med, med, med])
        assert scored.penalty_total == 24
        assert scored.compliance_score == 76
        assert scored.risk_level == "medium"
        assert scored.grade == "WARNING"
        assert scored.qc_result == "violation"
        assert scored.qc_status == "DONE"

    def test_r05_threat_tag_not_suspected(self):
        r05 = WordHit(
            speaker="agent",
            word="R05",
            severity="low",
            score_eligible=True,
            source="rule",
            rule_id="R05",
            redline_tag="threat",
        )
        assert suspected_eligible([r05]) is False
        scored = ScoringService().score([r05])
        assert scored.compliance_score == 98
        assert scored.risk_level == "low"


class TestOfflineQcService:
    async def test_pipeline_single_high_80_high_pass(self):
        svc = OfflineQcService(llm=LLMReviewer(invoke=_empty_llm))
        result = await svc.run(_ready_session())
        assert result.code == "0"
        assert result.inserted is False
        assert result.compliance_score == 80
        assert result.risk_level == "high"
        assert result.grade == "PASS"
        assert result.qc_result == "pending_review"
        assert result.qc_status == "PENDING_REVIEW"
        assert "R10" in result.skipped_rules
        assert not any(hit.redline_tag == "unverified" for hit in result.hits)
        assert not any(hit.rule_id == "R10" for hit in result.hits)
        assert result.to_http()["risk"] == "high"
        assert result.to_event()["score"] == 80

    async def test_no_agent_violation_is_compliant_done(self):
        svc = OfflineQcService(llm=LLMReviewer(invoke=_empty_llm))
        result = await svc.run(
            _ready_session(
                turns=_turns(
                    ("agent", "您好", 1),
                    ("user", "我想投诉你们", 2),
                )
            )
        )
        assert result.code == "0"
        assert result.qc_result == "compliant"
        assert result.qc_status == "DONE"
        assert result.compliance_score == 100
        assert result.risk_level == "low"
        assert result.grade == "EXCELLENT"
        assert result.alerts == []
        assert result.suspected_eligible is False
        assert not any(hit.speaker == "agent" and hit.score_eligible for hit in result.hits)
        assert "R10" in result.skipped_rules

    async def test_wait_text_writes_redis_not_table(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        svc = OfflineQcService(job_store=store)
        result = await svc.run(
            {
                "session_id": "sess_wait",
                "call_ended": False,
                "text_ready": False,
                "turns": [],
            }
        )
        assert result.job_status == "WAIT_TEXT"
        assert result.inserted is False
        assert result.qc_status is None
        raw = await redis.get(job_key("sess_wait"))
        assert raw is not None
        assert "WAIT_TEXT" in raw

    async def test_replay_wait_text_shares_job_store(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        replay = ReplayService(job_store=store)
        assert replay.job_store is store
        assert replay.offline.job_store is store
        result = await replay.run(
            {
                "session_id": "sess_replay_wait",
                "call_ended": False,
                "turns": [],
            }
        )
        assert result.job_status == "WAIT_TEXT"
        assert result.inserted is False
        assert result.qc_status is None
        raw = await redis.get(job_key("sess_replay_wait"))
        assert raw is not None
        assert "WAIT_TEXT" in raw

    async def test_retry_max_returns_partial_a2003(self):
        svc = OfflineQcService()
        result = await svc.run(
            {"session_id": "sess_max", "call_ended": False, "turns": []},
            text_retry_count=qc_settings.QC_TEXT_READY_RETRY_MAX,
        )
        assert result.code == "A2003"
        assert result.message == ERROR_MESSAGES["A2003"]
        assert result.message != "A2003"
        assert result.qc_status == "PARTIAL"
        assert result.inserted is False

    async def test_crash_returns_partial_a2001(self):
        svc = OfflineQcService(ac=BoomAC(), llm=LLMReviewer(invoke=_empty_llm))  # type: ignore[arg-type]
        result = await svc.run(_ready_session())
        assert result.code == "A2001"
        assert result.qc_status == "PARTIAL"
        assert result.inserted is False

    async def test_raw_without_call_records_a2003_no_insert(self):
        result = await ReplayService().run(_ready_session(), call_records_exists=False)
        assert result.code == "A2003"
        assert result.inserted is False
        assert result.hits == []

    async def test_llm_placeholder_returns_empty(self):
        hits = await LLMReviewer().review([])
        assert hits == []

    async def test_suspected_r11_computed_not_published(self):
        hit = WordHit(
            speaker="agent",
            word="R11",
            severity="high",
            score_eligible=True,
            source="rule",
            rule_id="R11",
            redline_tag="threat",
        )
        assert suspected_eligible([hit]) is True
        src = __import__("inspect").getsource(OfflineQcService._pipeline)
        assert "PUBLISH" not in src
        assert "qc.alert.raised" not in src
        assert "qc.check.completed" not in src
        assert "qc.complaint.suspected" not in src
        persist_src = __import__("inspect").getsource(ReplayService._persist)
        assert "_publish_terminal_events" in persist_src
        term_src = __import__("inspect").getsource(ReplayService._publish_terminal_events)
        assert "publish_complaint_suspected" in term_src
        from app.events.publisher import FORBIDDEN_CHANNELS

        assert "qc.escalate.human" in FORBIDDEN_CHANNELS

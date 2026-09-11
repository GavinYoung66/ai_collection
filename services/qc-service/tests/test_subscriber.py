"""QC-7.2：订阅四频道 handler；未齐 WAIT_TEXT 不插表；P1-2 单条失败跳过。"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.events.publisher import FORBIDDEN_CHANNELS
from app.events.subscriber import (
    CHANNEL_ETL_DONE,
    CHANNEL_KB_UPDATED,
    CHANNEL_SESSION_ENDED,
    CHANNEL_STRATEGY_UPDATED,
    SUBSCRIBE_CHANNELS,
    QcEventSubscriber,
)
from app.integrations.qc_session import QCSession
from app.models.qc import QcRecord
from app.repositories.job_store import QcJobStore, job_key
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import OfflineQcService, QcRunResult
from app.services.replay_service import ReplayService
from app.services.word_loader import WordLoader
from tests.test_job_store import MemoryRedis
from tests.test_offline_qc import _empty_llm
from tests.test_replay_api import ApiFakeSession, FakeQualityRepo, _qc_records, _ready


def _wait_payload(**kwargs) -> dict:
    base = {
        "session_id": "sess_wait",
        "case_id": "case_wait",
        "call_ended": False,
        "text_ready": False,
        "turns": [],
        "result": None,
        "call_id": str(uuid4()),
    }
    base.update(kwargs)
    return base


class RecordingReplay:
    def __init__(self) -> None:
        self.sessions: list[str] = []

    async def replay_session(self, qc: QCSession, **_kwargs) -> QcRunResult:
        self.sessions.append(qc.session_id)
        return QcRunResult(code="0", inserted=True, session_id=qc.session_id, job_status="DONE")


class SubDp:
    def __init__(
        self,
        sessions: dict[str, QCSession] | None = None,
        *,
        pending_ids: list[str] | None = None,
        fail_ids: set[str] | None = None,
        pending_open: bool = False,
    ) -> None:
        self.sessions = sessions or {}
        self.pending_ids = list(pending_ids or [])
        self.fail_ids = set(fail_ids or [])
        self.pending_circuit_open = pending_open
        self.get_calls: list[str] = []
        self.pending_calls = 0

    async def get_qc_source(self, session_id: str) -> QCSession:
        self.get_calls.append(session_id)
        if session_id in self.fail_ids:
            raise httpx.ConnectError("qc-source failed")
        return self.sessions[session_id]

    async def list_pending_session_ids(self, *, etl_date: str | None = None, limit: int = 100) -> list[str]:
        self.pending_calls += 1
        if self.pending_circuit_open:
            return []
        return list(self.pending_ids)


class NoInsertRepo(FakeQualityRepo):
    async def insert_record(self, **kwargs) -> QcRecord:
        raise AssertionError("WAIT_TEXT must not INSERT qc_records")


def _subscriber(*, dp, replay=None, loader=None, store=None, settings=None) -> QcEventSubscriber:
    return QcEventSubscriber(
        dp=dp,
        replay=replay,
        word_loader=loader,
        job_store=store,
        settings=settings,
    )


class TestSubscribeChannels:
    def test_four_channels_only_and_no_escalate(self):
        assert SUBSCRIBE_CHANNELS == (
            CHANNEL_SESSION_ENDED,
            CHANNEL_ETL_DONE,
            CHANNEL_KB_UPDATED,
            CHANNEL_STRATEGY_UPDATED,
        )
        assert "qc.escalate.human" not in SUBSCRIBE_CHANNELS
        assert "qc.llm.degraded" not in SUBSCRIBE_CHANNELS
        assert FORBIDDEN_CHANNELS.isdisjoint(SUBSCRIBE_CHANNELS)

    def test_no_dp_http_callback_surface(self):
        src = inspect.getsource(QcEventSubscriber)
        assert "callback" not in src.lower() or "no DpClient" in src
        assert "/qc/check" not in src
        assert "webhook" not in src.lower()
        assert not hasattr(QcEventSubscriber, "callback")
        assert not hasattr(QcEventSubscriber, "webhook")
        from app.main import app

        paths = [getattr(route, "path", "") for route in app.router.routes]
        assert not any(path in {"/qc/check", "/api/v1/qc/check"} for path in paths)


class TestSessionEnded:
    @pytest.mark.asyncio
    async def test_not_ready_ended_writes_wait_text_without_insert(self):
        qc = QCSession.model_validate(_wait_payload())
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        db = ApiFakeSession()
        db.call_ids.add(qc.session_id)
        repo = NoInsertRepo(db, store)
        offline = OfflineQcService(
            llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0),
            job_store=store,
        )
        replay = ReplayService(offline, job_store=store, quality_repo=repo)
        dp = SubDp({qc.session_id: qc})
        sub = _subscriber(dp=dp, replay=replay, store=store)
        out = await sub.handle_session_ended(
            {
                "session_id": qc.session_id,
                "case_id": qc.case_id,
                "result": None,
                "intent_tags": [],
                "trace_id": "t-ended",
                "ts": 1,
            }
        )
        assert out["inserted"] is False
        assert out["job_status"] == "WAIT_TEXT"
        raw = redis.data[job_key(qc.session_id)]
        assert "WAIT_TEXT" in raw
        assert _qc_records(db) == []

    @pytest.mark.asyncio
    async def test_ready_ended_runs_replay(self):
        qc = QCSession.model_validate(_ready())
        replay = RecordingReplay()
        dp = SubDp({qc.session_id: qc})
        sub = _subscriber(dp=dp, replay=replay)
        out = await sub.handle_session_ended(
            {
                "session_id": qc.session_id,
                "case_id": qc.case_id,
                "result": "connected",
                "intent_tags": ["ptp"],
                "trace_id": "t-ready",
                "ts": 2,
            }
        )
        assert replay.sessions == [qc.session_id]
        assert out["inserted"] is True

    @pytest.mark.asyncio
    async def test_auto_start_false_ready_does_not_replay(self):
        qc = QCSession.model_validate(_ready())
        replay = RecordingReplay()
        dp = SubDp({qc.session_id: qc})
        flags = SimpleNamespace(
            QC_AUTO_START_ON_ENDED=False,
            QC_AUTO_COMPLEMENT_AFTER_ETL=True,
        )
        sub = _subscriber(dp=dp, replay=replay, settings=flags)
        out = await sub.handle_session_ended({"session_id": qc.session_id})
        assert replay.sessions == []
        assert out["status"] == "skipped"


class TestEtlDone:
    @pytest.mark.asyncio
    async def test_etl_pending_then_qc_source_then_replay(self):
        qc = QCSession.model_validate(_ready(session_id="sess_etl"))
        replay = RecordingReplay()
        dp = SubDp({qc.session_id: qc}, pending_ids=[qc.session_id])
        sub = _subscriber(dp=dp, replay=replay)
        out = await sub.handle_etl_done(
            {"run_id": "run-1", "stage": "done", "backfill_date": "2026-09-09", "trace_id": "t", "ts": 3}
        )
        assert dp.pending_calls == 1
        assert dp.get_calls == [qc.session_id]
        assert replay.sessions == [qc.session_id]
        assert qc.session_id in out["complemented"]

    @pytest.mark.asyncio
    async def test_pending_breaker_skips_complement(self):
        qc = QCSession.model_validate(_ready())
        replay = RecordingReplay()
        dp = SubDp({qc.session_id: qc}, pending_ids=[qc.session_id], pending_open=True)
        sub = _subscriber(dp=dp, replay=replay)
        out = await sub.handle_etl_done({"run_id": "run-2", "stage": "done", "trace_id": "t", "ts": 4})
        assert out["complemented"] == []
        assert dp.get_calls == []
        assert replay.sessions == []

    @pytest.mark.asyncio
    async def test_single_qc_source_failure_skipped_not_success(self):
        ok = QCSession.model_validate(_ready(session_id="sess_ok"))
        later = QCSession.model_validate(_ready(session_id="sess_later"))
        replay = RecordingReplay()
        dp = SubDp(
            {ok.session_id: ok, later.session_id: later},
            pending_ids=["sess_ok", "sess_fail", "sess_later"],
            fail_ids={"sess_fail"},
        )
        db = ApiFakeSession()
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        repo = FakeQualityRepo(db, store)

        async def _replay(qc: QCSession) -> QcRunResult:
            await replay.replay_session(qc)
            row = await repo.insert_record(
                session_id=qc.session_id,
                compliance_score=100,
                risk_level="low",
                qc_result="compliant",
                qc_status="DONE",
            )
            return QcRunResult(code="0", inserted=True, session_id=qc.session_id, qc_record_id=str(row.id))

        sub = QcEventSubscriber(dp=dp, replay_session=_replay, job_store=store)
        out = await sub.handle_etl_done({"run_id": "run-3", "stage": "done", "trace_id": "t", "ts": 5})
        assert replay.sessions == ["sess_ok", "sess_later"]
        assert "sess_fail" not in out["complemented"]
        assert "sess_fail" in out["skipped"]
        inserted_ids = {row.session_id for row in _qc_records(db)}
        assert inserted_ids == {"sess_ok", "sess_later"}
        assert "sess_fail" not in inserted_ids

    @pytest.mark.asyncio
    async def test_etl_a2003_not_inserted_not_complemented(self):
        ok = QCSession.model_validate(_ready(session_id="sess_ok"))
        no_master = QCSession.model_validate(_ready(session_id="no_master"))
        replay = RecordingReplay()
        dp = SubDp(
            {ok.session_id: ok, no_master.session_id: no_master},
            pending_ids=["sess_ok", "no_master"],
        )

        async def _replay(qc: QCSession) -> QcRunResult:
            await replay.replay_session(qc)
            if qc.session_id == "no_master":
                return QcRunResult(code="A2003", inserted=False, session_id=qc.session_id)
            return QcRunResult(
                code="0",
                inserted=True,
                session_id=qc.session_id,
                qc_record_id="rid-ok",
            )

        sub = QcEventSubscriber(dp=dp, replay_session=_replay)
        out = await sub.handle_etl_done({"run_id": "run-a2003", "stage": "done", "trace_id": "t", "ts": 9})
        assert replay.sessions == ["sess_ok", "no_master"]
        assert "sess_ok" in out["complemented"]
        assert "no_master" not in out["complemented"]
        assert "no_master" in out["skipped"]

    @pytest.mark.asyncio
    async def test_m1_without_ended_etl_still_starts(self):
        qc = QCSession.model_validate(_ready(session_id="sess_no_ended"))
        replay = RecordingReplay()
        dp = SubDp({qc.session_id: qc}, pending_ids=[qc.session_id])
        sub = _subscriber(dp=dp, replay=replay)
        out = await sub.handle_etl_done({"run_id": "run-4", "stage": "done", "trace_id": "t", "ts": 6})
        assert replay.sessions == [qc.session_id]
        assert qc.session_id in out["complemented"]

    @pytest.mark.asyncio
    async def test_handle_message_does_not_consume_escalate(self):
        replay = RecordingReplay()
        sub = _subscriber(dp=SubDp(), replay=replay)
        out = await sub.handle_message("qc.escalate.human", {"session_id": "x"})
        assert out["status"] == "ignored"
        assert replay.sessions == []


class TestStrategyAndKbDispatch:
    @pytest.mark.asyncio
    async def test_strategy_updated_writes_rules_active(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        sub = _subscriber(dp=SubDp(), store=store)
        out = await sub.handle_strategy_updated(
            {"version": "v1.0", "strategy_type": "qc_rules", "trace_id": "t", "ts": 7}
        )
        assert out["version"] == "v1.0"
        assert await store.get_rules_active() == "v1.0"

    @pytest.mark.asyncio
    async def test_kb_updated_delegates_to_word_loader(self):
        calls: list[dict] = []

        class FakeLoader:
            async def on_kb_sensitive_updated(self, payload):
                calls.append(payload)
                return {"ok": True, "version": payload["version"]}

        sub = _subscriber(dp=SubDp(), loader=FakeLoader())
        out = await sub.handle_kb_updated({"version": "dict-9", "trace_id": "t", "ts": 8})
        assert calls == [{"version": "dict-9", "trace_id": "t", "ts": 8}]
        assert out["version"] == "dict-9"

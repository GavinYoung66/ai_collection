"""T97/T100：replay 明细幂等与 SET NX 原子占坑（spec runtime-correctness：事件重投下明细写入幂等）。

- T97：同一 `call.session.ended` 被投递两次（replay_session 两次），第二次不新增
  qc_violations/qc_alerts 行、告警不重复推送（DONE 短路 inserted=False，事件不重发）；
  force 重跑明细去重，行数不翻倍。
- T100：幂等登记 SET NX 原子占坑——并发同幂等键只执行一次，另一个读回同一结果；
  占坑失败且无结果（窄窗口）时放行执行，落库由 qc_records.session_id 唯一约束
  IntegrityError 转读回兜底（design D2.3）。
"""
from __future__ import annotations


from uuid import uuid4

import pytest
from sqlalchemy.exc import IntegrityError

from app.integrations.qc_session import QCSession
from app.models.qc import QcAlert, QcRecord, QcViolation
from app.repositories.job_store import QcJobStore, replay_run_key
from app.repositories.quality_repo import QualityRepo
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import OfflineQcService
from app.services.replay_service import ReplayService
from tests.test_job_store import MemoryRedis


async def _empty_llm(_messages):
    return "[]"


def _ready(**kwargs) -> dict:
    base = {
        "session_id": "sess_ok",
        "case_id": "case_ok",
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "turns": [
            {
                "turn_id": "t1",
                "speaker": "agent",
                "text": "让你妈妈接电话",
                "turn_index": 1,
            }
        ],
        "result": None,
        "call_id": None,
    }
    base.update(kwargs)
    return base


def _qc() -> QCSession:
    return QCSession.model_validate(_ready())


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar(self):
        return self._value

    def scalar_one_or_none(self):
        return self._value


class _RowsResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class IdemFakeSession:
    """幂等测试内存 session：支持 call_records/qc_records/qc_violations/qc_alerts
    四类查询与 IntegrityError 注入（模拟 qc_records.session_id 唯一约束）。"""

    def __init__(self):
        self.added: list = []
        self.db_record: QcRecord | None = None
        self._pending_record: QcRecord | None = None
        # 并发冲突场景：对方已落库的终态行（rollback 后读回目标）
        self.peer_record: QcRecord | None = None

        self.integrity_on_next_flush = False
        self.rollback_count = 0

    def add(self, obj):
        # 模拟 ORM column default：真实 flush 时 id default=uuid4 由 SQLAlchemy
        # 填充；fake flush 不执行 default，须在 add 时补齐，否则 violation_id
        # 关联链（QcViolation.id → QcAlert.violation_id）断裂、判重失效。
        if isinstance(obj, (QcRecord, QcViolation, QcAlert)) and getattr(obj, "id", None) is None:
            obj.id = uuid4()
        self.added.append(obj)
        if isinstance(obj, QcRecord):
            self._pending_record = obj

    async def flush(self):
        if self.integrity_on_next_flush:
            self.integrity_on_next_flush = False
            raise IntegrityError(
                "INSERT INTO qc_records ...", None,
                RuntimeError("duplicate key violates unique constraint qc_records_session_id_key (23505)"),
            )
        if self._pending_record is not None:
            self.db_record = self._pending_record
        return None

    async def commit(self):
        return None

    async def rollback(self):
        self.rollback_count += 1
        return None

    async def execute(self, stmt, params=None):  # noqa: ARG002 — call_record_exists 双参形态
        s = str(stmt).lower()
        if "call_records" in s:
            return _ScalarResult(1)
        if "qc_records" in s:
            if self.peer_record is not None:
                # 并发场景时序：对方行在 INSERT 提交时才落库——本方查询先返回
                # None（短路判定不命中），flush 抛 23505，rollback 后重读才可见
                if self.rollback_count > 0:
                    return _ScalarResult(self.peer_record)
                return _ScalarResult(None)
            return _ScalarResult(self.db_record)
        if "qc_violations" in s:
            return _RowsResult([o for o in self.added if isinstance(o, QcViolation)])
        if "qc_alerts" in s:
            return _RowsResult([o for o in self.added if isinstance(o, QcAlert)])
        if "qc_rules" in s:
            return _ScalarResult(None)
        raise AssertionError(f"unexpected SQL in idempotency fakes: {s[:120]}")

    def violations(self) -> list[QcViolation]:
        return [o for o in self.added if isinstance(o, QcViolation)]

    def alerts(self) -> list[QcAlert]:
        return [o for o in self.added if isinstance(o, QcAlert)]


def _svc(session: IdemFakeSession, redis: MemoryRedis) -> ReplayService:
    store = QcJobStore(redis)
    offline = OfflineQcService(llm=LLMReviewer(invoke=_empty_llm), job_store=store)
    return ReplayService(offline=offline, job_store=store, quality_repo=QualityRepo(session))


class TestT97DoneShortCircuit:
    @pytest.mark.asyncio
    async def test_repeated_delivery_no_new_rows_no_rebroadcast(self, _mock_event_bus):
        """同一 call.session.ended 投递两次：第二次不新增明细行、不重发事件。"""
        session = IdemFakeSession()
        redis = MemoryRedis(db=3)
        svc = _svc(session, redis)
        qc = _qc()

        r1 = await svc.replay_session(qc, persist=True)
        added_after_first = len(session.added)
        bus_after_first = _mock_event_bus.await_count

        r2 = await svc.replay_session(qc, persist=True)

        assert r1.inserted is True
        assert r1.qc_record_id is not None
        # 第二次：落库终态（R13 红线场景为 PENDING_REVIEW）短路回读——不新增
        # qc_records/violations/alerts 行，状态与第一次一致
        assert r2.inserted is False
        assert r2.qc_record_id == r1.qc_record_id
        assert r2.qc_status == r1.qc_status
        assert len(session.violations()) >= 1
        assert len(session.added) == added_after_first
        # 告警不重复推送：短路路径 _publish_terminal_events 不可达（inserted=False）
        assert _mock_event_bus.await_count == bus_after_first

    @pytest.mark.asyncio
    async def test_force_rerun_dedupes_details(self, _mock_event_bus):
        """force rerun（显式重放意图）走重跑分支，明细按指纹去重不翻倍。"""
        session = IdemFakeSession()
        redis = MemoryRedis(db=3)
        svc = _svc(session, redis)
        qc = _qc()

        r1 = await svc.replay_session(qc, persist=True)
        v_before, a_before = len(session.violations()), len(session.alerts())
        r2 = await svc.replay_session(qc, force=True, persist=True)

        assert r1.inserted is True
        assert r2.inserted is True
        assert r2.qc_record_id == r1.qc_record_id
        # 明细去重：(rule_id, turn_id, speaker) 重复 hit 跳过 INSERT
        assert len(session.violations()) == v_before
        assert len(session.alerts()) == a_before

    @pytest.mark.asyncio
    async def test_integrity_error_reads_back_peer_record(self, _mock_event_bus):
        """SET NX 窗口外并发双跑：qc_records.session_id 唯一约束（23505）收敛，
        IntegrityError 转读回并发方已落库终态行，明细不双写（design D2.3）。"""
        session = IdemFakeSession()
        peer = QcRecord(
            session_id="sess_ok",
            compliance_score=80,
            deduction_total=20,
            risk_level="high",
            qc_result="non_compliant",
            qc_status="DONE",
        )
        peer.id = uuid4()
        session.peer_record = peer
        session.integrity_on_next_flush = True
        redis = MemoryRedis(db=3)
        svc = _svc(session, redis)

        result = await svc.replay_session(_qc(), persist=True)

        assert result.inserted is False
        assert result.qc_record_id == str(peer.id)
        assert result.qc_status == "DONE"
        assert session.rollback_count == 1
        # 转读回路径不写任何明细
        assert session.violations() == []
        assert session.alerts() == []


class TestT100SetNx:
    @pytest.mark.asyncio
    async def test_acquire_replay_run_nx_semantics(self):
        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)

        assert await store.acquire_replay_run("sess_ok", "t1") is True
        # 键已被占：并发同 key 占坑失败
        assert await store.acquire_replay_run("sess_ok", "t2") is False
        assert await store.get_replay_run("sess_ok") == "t1"
        # 显式覆盖（不同 key 新重放）语义保持
        await store.set_replay_run("sess_ok", "t2")
        assert await store.get_replay_run("sess_ok") == "t2"

    @pytest.mark.asyncio
    async def test_same_idempotency_key_executes_once_reads_back(self, _mock_event_bus):
        """并发同幂等键只执行一次：第二次读回同一结果，明细行不翻倍。"""
        session = IdemFakeSession()
        redis = MemoryRedis(db=3)
        svc = _svc(session, redis)
        qc = _qc()

        r1 = await svc.replay_raw(qc, idempotency_key="idem-k1")
        records_after_first = [o for o in session.added if isinstance(o, QcRecord)]
        r2 = await svc.replay_raw(qc, idempotency_key="idem-k1")

        assert r1.inserted is True
        assert r2.inserted is False
        assert r2.qc_record_id == r1.qc_record_id
        assert len(records_after_first) == 1
        assert len([o for o in session.added if isinstance(o, QcRecord)]) == 1

    @pytest.mark.asyncio
    async def test_different_idempotency_key_takes_over_registration(self, _mock_event_bus):
        """不同幂等键=显式新重放请求：覆盖登记。对已落库终态记录，非 force 重放
        仍被 T97 终态短路（幂等）；force=True 才真正重跑（明细去重不双写）。"""
        session = IdemFakeSession()
        redis = MemoryRedis(db=3)
        svc = _svc(session, redis)
        qc = _qc()

        r1 = await svc.replay_raw(qc, idempotency_key="k1")
        assert r1.inserted is True
        assert redis.data[replay_run_key("sess_ok")] == "k1"
        v_before, a_before = len(session.violations()), len(session.alerts())

        # 非 force：不同 key 重放终态记录 → 终态短路回读（明细不双写）
        r2 = await svc.replay_raw(qc, idempotency_key="k2")
        assert redis.data[replay_run_key("sess_ok")] == "k2"  # 登记已被新 key 覆盖
        assert r2.inserted is False
        assert r2.qc_record_id == r1.qc_record_id
        assert len(session.violations()) == v_before
        assert len(session.alerts()) == a_before

        # force=True：显式强制重跑 → 走去重重跑分支
        r3 = await svc.replay_raw(qc, force=True, idempotency_key="k3")
        assert r3.inserted is True
        assert r3.qc_record_id == r1.qc_record_id
        assert len(session.violations()) == v_before
        assert len(session.alerts()) == a_before

    @pytest.mark.asyncio
    async def test_nx_lose_without_result_proceeds(self, _mock_event_bus):
        """占坑失败且无已有结果（执行方在途窄窗口）：放行执行不挂起，
        落库由唯一约束兜底。"""

        class RaceRedis(MemoryRedis):
            async def set(self, key, value, ex=None, nx=False, **kwargs):
                if nx and key == replay_run_key("sess_ok"):
                    return None  # 模拟并发方抢先占坑
                return await super().set(key, value, ex=ex, nx=nx, **kwargs)

        session = IdemFakeSession()
        svc = _svc(session, RaceRedis(db=3))
        result = await svc.replay_raw(_qc(), idempotency_key="race-k")

        assert result.inserted is True
        # R13 红线命中场景落库态为 PENDING_REVIEW（等待人工复核）
        assert result.qc_status == "PENDING_REVIEW"

    @pytest.mark.asyncio
    async def test_run_key_lost_done_short_circuits(self, _mock_event_bus):
        """运行态键丢失（TTL 过期/Redis 抖动）后同 key 重放：T97 DONE 短路兜底。"""
        session = IdemFakeSession()
        redis = MemoryRedis(db=3)
        svc = _svc(session, redis)
        qc = _qc()

        r1 = await svc.replay_raw(qc, idempotency_key="k1")
        await redis.delete(replay_run_key("sess_ok"))  # 模拟键消失
        v_before, a_before = len(session.violations()), len(session.alerts())
        r2 = await svc.replay_raw(qc, idempotency_key="k1")

        assert r1.inserted is True
        assert r2.inserted is False
        assert r2.qc_record_id == r1.qc_record_id
        assert len(session.violations()) == v_before
        assert len(session.alerts()) == a_before

"""complaint_sessions 持久化与 hydrate 验收测试。

需真实 PG 的用例：`COMPLAINT_DB_TESTS=1` + `COMPLAINT_PG_DSN`。
`test_task_status_enum_consistency` 不需要 PG。

全部用例写成 async（pytest-asyncio AUTO 模式）：psycopg 的 AsyncConnection 绑定
创建它的 event loop，若在 sync 测试里用 `asyncio.run()` 会跨 loop 使用连接而报错。

覆盖三条关键路径：
1. 建单后 session 行落库；
2. `pending_callback` 能写入（迁移 0015 的 CHECK 修正回归）；
3. 清空全部进程内状态后仍能 hydrate 且不 404（重启/换副本的核心断言）。
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.models.state import TaskStatus
from app.service.session_service import session_service


@pytest.fixture()
def pg_required():
    """需要真实 PG 的用例开关（对齐 rag-service 的 RAG_DB_TESTS 约定）。

    DSN 由 PG_* 设置派生（见 settings.pg_dsn_psycopg3），不要求全局 COMPLAINT_PG_DSN
    ——后者一设上会让每个构造图的用例都去建 PG 连接池，拖慢整个套件。
    """
    if os.getenv("COMPLAINT_DB_TESTS") != "1":
        pytest.skip("COMPLAINT_DB_TESTS != 1，跳过需要真实 PG 的用例")


@pytest.fixture()
async def pg_conn(pg_required):
    import psycopg

    from app.config import settings

    conn = await psycopg.AsyncConnection.connect(settings.pg_dsn_psycopg3, autocommit=True)
    try:
        yield conn
    finally:
        await conn.close()


@pytest.fixture()
async def own_case_id(pg_conn):
    """自建一条 cases 行，**不复用既有 case**。

    复用既有 case 会让 complaints 的幂等 INSERT 命中已有行，而用例结束时的
    `DELETE FROM complaints WHERE id=<case_id>` 就会误删真实工单数据。
    """
    cur = await pg_conn.execute(
        "INSERT INTO cases (id, case_number, customer_name, phone_encrypted, "
        "phone_hash, overdue_amount, overdue_days, account_stage) "
        "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (f"IT-{uuid.uuid4().hex[:12]}", "集成测试", "enc", "hash",
         1000, 30, "M1"),
    )
    row = await cur.fetchone()
    case_id = str(row[0])
    try:
        yield case_id
    finally:
        # cases → complaints 有 ON DELETE CASCADE，删除 case 会连带清掉本次工单与会话
        await pg_conn.execute("DELETE FROM cases WHERE id = %s", (case_id,))


@pytest.fixture()
def enable_persist(monkeypatch):
    """打开写穿：USE_MOCK_MCP=False（与 _persist_complaint_row 同一道门）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "COMPLAINT_SESSION_PERSIST", True)


@pytest.fixture()
def cleanup_memory():
    """用例后清掉本进程内的会话/缓存残留。"""
    from app.service.complaint_service import complaint_service

    cids: list[str] = []
    yield cids
    for cid in cids:
        rec = complaint_service._cache.pop(cid, None)
        sid = (rec or {}).get("session_id")
        if sid:
            session_service._store.pop(sid, None)
            session_service._by_complaint.pop(cid, None)
        for s in list(session_service._store):
            if session_service._store[s].get("complaint_id") == cid:
                session_service._store.pop(s, None)


def test_task_status_enum_consistency():
    """ORM 枚举 / 迁移 CHECK / TaskStatus 三处必须同源，防止新增状态漏改。"""
    from app.models.complaint_session import _TASK_STATUSES

    assert set(_TASK_STATUSES) == {s.value for s in TaskStatus}


async def test_concurrent_flush_does_not_exhaust_or_fail(pg_conn, own_case_id,
                                                         enable_persist, cleanup_memory):
    """并发写穿不报错（验证连接池在并发下行为正常，而非耗尽即崩）。

    common/db 的 asyncpg 池为 pool_size=10 + max_overflow=20；此处并发 20 路
    尚在容量内，重点验证不会因并发产生异常或被 fail-open 静默吞掉。
    """
    import asyncio

    from app.service.complaint_service import complaint_service

    rec = await complaint_service.create_async("并发写穿", case_id=own_case_id)
    sid = rec["session_id"]
    cleanup_memory.append(rec["complaint_id"])

    results = await asyncio.gather(
        *[session_service.aflush_session(sid) for _ in range(20)],
        return_exceptions=True,
    )
    assert all(r is True for r in results), f"存在失败/异常: {results}"
    assert session_service.persist_degraded is False


async def test_aload_by_complaint_tolerates_invalid_uuid(pg_required):
    """非法 UUID 的 complaint_id 返回 None，不抛异常。

    生产里 complaint_id 通常是 UUID，但测试/冒烟会用可读串（如 c-b2-xxx），
    hydrate 路径不能因此炸成 500。
    """
    assert await session_service.aload_by_complaint("not-a-uuid") is None
    assert await session_service.aload_session("nonexistent-sid") is None


async def test_create_persists_session_row(pg_conn, own_case_id, enable_persist,
                                           cleanup_memory):
    """建单后 complaint_sessions 落一行，task_status='pending'。"""
    from app.service.complaint_service import complaint_service

    rec = await complaint_service.create_async("持久化用例-建单", case_id=own_case_id)
    cid, sid = rec["complaint_id"], rec["session_id"]
    cleanup_memory.append(cid)

    cur = await pg_conn.execute(
        "SELECT task_status, complaint_id FROM complaint_sessions WHERE session_id = %s",
        (sid,),
    )
    row = await cur.fetchone()
    assert row is not None, "session 未落库"
    assert row[0] == TaskStatus.pending.value
    assert str(row[1]) == cid


async def test_pending_callback_persists(pg_conn, own_case_id, enable_persist,
                                         cleanup_memory):
    """迁移 0015 CHECK 修正的回归测试。

    0010 的约束缺 pending_callback，而 V4 定责会写这个状态；不修则写穿
    恰好在定责路径失败（内存成功、PG 拒绝、异常被 flush 吞掉）。
    """
    from app.service.complaint_service import complaint_service

    rec = await complaint_service.create_async("持久化用例-定责", case_id=own_case_id)
    sid = rec["session_id"]
    cleanup_memory.append(rec["complaint_id"])

    session_service.transition(sid, TaskStatus.evidence_gathering)
    session_service.transition(sid, TaskStatus.judging)
    session_service.transition(sid, TaskStatus.pending_review)
    session_service.transition(sid, TaskStatus.pending_callback)

    ok = await session_service.aflush_session(sid)
    assert ok is True, "flush 失败（很可能撞了 task_status CHECK 约束）"

    cur = await pg_conn.execute(
        "SELECT task_status FROM complaint_sessions WHERE session_id = %s", (sid,)
    )
    row = await cur.fetchone()
    assert row is not None and row[0] == "pending_callback"
    assert session_service.persist_degraded is False


async def test_hydrate_after_restart(pg_conn, own_case_id, enable_persist,
                                     cleanup_memory):
    """清空全部进程内状态后，aget_or_raise 仍能重建且不抛 ComplaintNotFound。

    这是「服务重启后人工复核不再 404」的直接断言：只持久化 session 不够，
    必须同时重建 session_service._store 与 complaint_service._cache。
    """
    from app.service.complaint_service import complaint_service

    rec = await complaint_service.create_async("持久化用例-hydrate", case_id=own_case_id)
    cid, sid = rec["complaint_id"], rec["session_id"]
    cleanup_memory.append(cid)

    session_service.transition(sid, TaskStatus.evidence_gathering)
    session_service.transition(sid, TaskStatus.judging)
    session_service.transition(sid, TaskStatus.pending_review)
    await session_service.aflush_session(sid)

    # 模拟重启：清空两处进程内状态
    complaint_service._cache.pop(cid, None)
    session_service._store.pop(sid, None)
    session_service._by_complaint.pop(cid, None)
    assert complaint_service.get(cid) is None

    rebuilt = await complaint_service.aget_or_raise(cid)
    assert rebuilt["complaint_id"] == cid
    assert rebuilt["task_status"] == TaskStatus.pending_review


async def test_flush_failure_is_fail_open(own_case_id, monkeypatch, enable_persist,
                                          cleanup_memory):
    """写穿失败不回滚内存流转、不抛异常，只置 persist_degraded。"""
    from app.service.complaint_service import complaint_service

    rec = complaint_service.create("持久化用例-failopen", case_id=own_case_id)
    cid, sid = rec["complaint_id"], rec["session_id"]
    cleanup_memory.append(cid)

    import common.db.session as db_mod

    def _boom():
        raise RuntimeError("pg down")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)

    ok = await session_service.aflush_session(sid)
    assert ok is False
    assert session_service.persist_degraded is True
    # 内存流转不受影响
    assert session_service.get_status(sid) == TaskStatus.pending.value


# ── Step 6：图产出快照回填 ─────────────────────────────────


def test_jsonable_handles_pydantic_enum_and_containers():
    """图状态值 → JSONB 可序列化形式（pydantic / Enum / datetime / 嵌套）。"""
    from datetime import datetime, timezone

    from app.models.state import CaseEvidenceBundle, VerdictType

    assert session_service._jsonable(VerdictType.agent_violation) == "agent_violation"
    assert session_service._jsonable(None) is None
    assert session_service._jsonable({"a": VerdictType.pending}) == {"a": "pending"}
    assert session_service._jsonable([VerdictType.pending]) == ["pending"]
    dt = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert isinstance(session_service._jsonable(dt), str)
    bundle = CaseEvidenceBundle(case_id="c1")
    dumped = session_service._jsonable(bundle)
    assert isinstance(dumped, dict) and dumped.get("case_id") == "c1"


async def test_graph_snapshot_persists_and_leaves_status_untouched(
    pg_conn, own_case_id, enable_persist, cleanup_memory
):
    """快照写入 JSONB 产出列，且**不碰** task_status（状态机列归 transition 独占）。"""
    from app.service.complaint_service import complaint_service

    rec = await complaint_service.create_async("快照用例", case_id=own_case_id)
    sid = rec["session_id"]
    cleanup_memory.append(rec["complaint_id"])

    # 先推进状态，验证快照不会覆盖它
    session_service.transition(sid, TaskStatus.evidence_gathering)
    await session_service.aflush_session(sid)

    snapshot = {
        "verdict_draft": {"verdict": "agent_violation", "confidence": 0.82,
                          "reasoning": "话术不当"},
        "loop_count": 2,
        "exception_info": [],
    }
    assert await session_service.aflush_graph_snapshot(sid, snapshot) is True

    cur = await pg_conn.execute(
        "SELECT verdict_draft, loop_count, task_status FROM complaint_sessions "
        "WHERE session_id = %s", (sid,)
    )
    row = await cur.fetchone()
    assert row is not None
    assert row[0]["verdict"] == "agent_violation"
    assert float(row[0]["confidence"]) == 0.82
    assert row[1] == 2
    # 状态机列必须保持 transition 写入的值，未被快照影响
    assert row[2] == TaskStatus.evidence_gathering.value
    assert session_service.persist_degraded is False


async def test_graph_snapshot_skips_absent_keys(pg_conn, own_case_id, enable_persist,
                                                cleanup_memory):
    """快照里没有的列不写（保持 NULL），不把缺失值写成 null 覆盖已有数据。"""
    from app.service.complaint_service import complaint_service

    rec = await complaint_service.create_async("快照部分用例", case_id=own_case_id)
    sid = rec["session_id"]
    cleanup_memory.append(rec["complaint_id"])

    assert await session_service.aflush_graph_snapshot(sid, {"loop_count": 3}) is True
    cur = await pg_conn.execute(
        "SELECT loop_count, verdict_draft FROM complaint_sessions WHERE session_id = %s",
        (sid,),
    )
    row = await cur.fetchone()
    assert row[0] == 3
    assert row[1] is None, "未提供的列应保持 NULL，而非被覆盖"


async def test_graph_snapshot_failure_is_fail_open(own_case_id, monkeypatch,
                                                   enable_persist, cleanup_memory):
    """快照写入失败不抛异常（研判已完成，不应因快照回填而失败）。"""
    from app.service.complaint_service import complaint_service

    rec = complaint_service.create("快照 failopen", case_id=own_case_id)
    cleanup_memory.append(rec["complaint_id"])

    import common.db.session as db_mod

    def _boom():
        raise RuntimeError("pg down")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)
    assert await session_service.aflush_graph_snapshot(
        rec["session_id"], {"loop_count": 1}
    ) is False
    assert session_service.persist_degraded is True


async def test_persist_degraded_self_heals_on_success(own_case_id, monkeypatch,
                                                      enable_persist, cleanup_memory):
    """降级标志必须可自愈：一次成功后复位。

    若做成只置不清的闩锁，一次瞬时 PG 抖动会让 /health 永久显示 degraded，
    运维无法据此判断现状。
    """
    from app.service.complaint_service import complaint_service

    rec = complaint_service.create("自愈用例", case_id=own_case_id)
    cleanup_memory.append(rec["complaint_id"])

    import common.db.session as db_mod

    real_factory = db_mod.async_session_factory

    def _boom():
        raise RuntimeError("pg down")

    monkeypatch.setattr(db_mod, "async_session_factory", _boom)
    assert await session_service.aflush_graph_snapshot(rec["session_id"],
                                                       {"loop_count": 1}) is False
    assert session_service.persist_degraded is True

    monkeypatch.setattr(db_mod, "async_session_factory", real_factory)
    await complaint_service.create_async("自愈用例2", case_id=own_case_id)
    assert session_service.persist_degraded is False, "成功写入后应复位"


async def test_judge_end_to_end_writes_graph_snapshot(pg_conn, own_case_id,
                                                     enable_persist, cleanup_memory):
    """端到端：真跑一次 judge，图产出快照落到 complaint_sessions 且与图终态一致。

    这是 Step 6 的验收断言，同时覆盖「judge → aflush_session → aflush_graph_snapshot」
    三者在 _judge_with_graph 里的真实调用顺序。
    """
    from app.graph.builder import build_complaint_graph
    from app.service.complaint_service import (
        _get_graph_checkpointer,
        complaint_service,
    )

    rec = await complaint_service.create_async("快照端到端", case_id=own_case_id)
    cid, sid = rec["complaint_id"], rec["session_id"]
    cleanup_memory.append(cid)

    await complaint_service.judge_async(cid, run_graph=True)

    cur = await pg_conn.execute(
        "SELECT task_status, loop_count, verdict_draft, exception_info "
        "FROM complaint_sessions WHERE session_id = %s", (sid,)
    )
    row = await cur.fetchone()
    assert row is not None, "session 行不存在"

    graph = build_complaint_graph(checkpointer=await _get_graph_checkpointer())
    snap = await graph.aget_state({"configurable": {"thread_id": cid}})
    values = dict(getattr(snap, "values", None) or {})

    # 与图终态逐项比对（存在才比，缺失不臆断）
    if "loop_count" in values:
        assert row[1] == values["loop_count"], "loop_count 与图终态不一致"
    # 图已产出 verdict_draft 时，快照列必须非空（不能停留在 NULL）
    if values.get("verdict_draft") is not None:
        assert row[2] is not None, "图已产出 verdict_draft 但快照列为空"

    assert session_service.persist_degraded is False

    # 清理本线程的 checkpoint（其余由 cleanup fixture 负责）
    for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
        await pg_conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (cid,))

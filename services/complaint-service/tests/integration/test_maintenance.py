"""周期维护任务（B1 超时转人工 + 三表裁剪）验收测试。

需真实 PG：`COMPLAINT_DB_TESTS=1`。

核心断言：进程崩溃/重启后，滞留的 judging/draft_generated 工单从 PG 真相源
被转 pending_review —— 这是「B1 走路线二」的落地，不依赖内存态（内存已随重启清空）。
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.models.state import TaskStatus
from app.service.maintenance import run_maintenance
from app.service.session_service import session_service


@pytest.fixture()
def pg_required():
    if os.getenv("COMPLAINT_DB_TESTS") != "1":
        pytest.skip("COMPLAINT_DB_TESTS != 1，跳过需要真实 PG 的维护用例")


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
def enable_persist(monkeypatch):
    """开启 session 落 PG（atimeout 只在 PG 持久化启用时有意义）。"""
    from app.config import settings

    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "COMPLAINT_SESSION_PERSIST", True)


@pytest.fixture()
async def make_session(pg_conn):
    """造一条 case + complaint + session（不造 checkpoint），返回 (sid, cid, case_id)。

    age_minutes 把 update_time 回拨，用于模拟「崩溃前就在 judging」。
    """
    async def _make(status: str, age_minutes: int):
        cur = await pg_conn.execute(
            "INSERT INTO cases (id, case_number, customer_name, phone_encrypted, "
            "phone_hash, overdue_amount, overdue_days, account_stage) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"IT-{uuid.uuid4().hex[:12]}", "维护测试", "enc", "hash", 1, 1, "M1"),
        )
        case_id = (await cur.fetchone())[0]
        cur = await pg_conn.execute(
            "INSERT INTO complaints (id, case_id, complaint_text) "
            "VALUES (%s, %s, %s) RETURNING id",
            (case_id, case_id, "维护测试"),
        )
        cid = (await cur.fetchone())[0]
        sid = f"s-{cid}"
        await pg_conn.execute(
            "INSERT INTO complaint_sessions "
            "(session_id, trace_id, complaint_id, complaint_text, task_status, update_time) "
            "VALUES (%s, %s, %s, %s, %s, now() - make_interval(mins => %s))",
            (sid, f"t-{cid}", cid, "维护测试", status, age_minutes),
        )
        return sid, str(cid), str(case_id)

    return _make


@pytest.fixture(autouse=True)
async def _cleanup(pg_conn):
    yield
    await pg_conn.execute("DELETE FROM cases WHERE customer_name = '维护测试'")


async def test_atimeout_converts_stale_judging(make_session, pg_conn, enable_persist):
    """崩溃前滞留 judging 超 30min → 转 pending_review。"""
    sid, _, _ = await make_session("judging", age_minutes=45)
    timed_out = await session_service.atimeout_sessions_stale()
    assert sid in timed_out

    cur = await pg_conn.execute(
        "SELECT task_status FROM complaint_sessions WHERE session_id = %s", (sid,)
    )
    assert (await cur.fetchone())[0] == "pending_review"


async def test_atimeout_spares_recent_and_terminal(make_session, pg_conn, enable_persist):
    """未超期的 judging 与终态 closed 都不转（即使 closed 超期）。"""
    recent_sid, _, _ = await make_session("judging", age_minutes=10)
    closed_sid, _, _ = await make_session("closed", age_minutes=500)
    draft_sid, _, _ = await make_session("draft_generated", age_minutes=60)

    timed_out = await session_service.atimeout_sessions_stale()

    assert recent_sid not in timed_out, "未超期不应转"
    assert closed_sid not in timed_out, "终态不可回退"
    assert draft_sid in timed_out, "超期 draft_generated 应转"


async def test_atimeout_noop_when_mock_mode(make_session, pg_conn):
    """mock 模式（USE_MOCK_MCP=true，session 不落 PG）下返回 []。"""
    sid, _, _ = await make_session("judging", age_minutes=45)
    # 默认 mock 模式（无 enable_persist fixture）
    assert await session_service.atimeout_sessions_stale() == []


async def test_run_maintenance_end_to_end(make_session, pg_conn, enable_persist):
    """一轮维护：超期工单转人工 + 终态线程的 checkpoint 被裁剪。"""
    from app.graph.checkpointer import prune_checkpoints

    # 超期 judging（应转人工）
    sid, cid, _ = await make_session("judging", age_minutes=45)
    # 终态超期（checkpoint 应被裁剪）：须超 90 天（prune 的 retention_days）
    _, closed_cid, _ = await make_session("closed", age_minutes=90 * 24 * 60 + 60)
    # 给 closed 线程造 checkpoint 行
    await pg_conn.execute(
        "INSERT INTO checkpoints (thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
        "VALUES (%s, '', 'cp-1', '{}'::jsonb, '{}'::jsonb)",
        (closed_cid,),
    )

    stats = await run_maintenance()

    assert sid in stats["timed_out"]
    assert stats["pruned_threads"] >= 1

    # 终态线程的 checkpoint 被清空
    cur = await pg_conn.execute(
        "SELECT count(*) FROM checkpoints WHERE thread_id = %s", (closed_cid,)
    )
    assert (await cur.fetchone())[0] == 0


async def test_run_maintenance_is_idempotent(make_session, pg_conn, enable_persist):
    """连续跑两轮，第二轮不重复处理（维护幂等，多副本安全的前提）。"""
    sid, _, _ = await make_session("judging", age_minutes=45)

    first = await run_maintenance()
    second = await run_maintenance()

    assert sid in first["timed_out"]
    assert sid not in second["timed_out"], "第二轮应无新超期工单"


async def test_prune_checkpoints_excluded_in_maintenance_when_mock(make_session, pg_conn):
    """mock 模式下 run_maintenance 不误删（prune 依赖 session 表，mock 下无行）。"""
    # 无 enable_persist：mock 模式，session 不落 PG，atimeout 返回 []
    stats = await run_maintenance()
    assert stats["timed_out"] == []
    # prune 只删「有对应 session 行且终态」的线程，mock 下无 session 行，故不删
    assert stats["pruned_threads"] == 0

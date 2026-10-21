"""checkpoint 三表裁剪（#1 无界增长治理）验收测试。

需真实 PG：`COMPLAINT_DB_TESTS=1`。

本组的安全断言比功能断言更重要：一旦裁剪逻辑误伤 `pending_review` 线程，
人工复核的 resume 能力就会静默丢失——正是本功能存在的全部意义。
"""
from __future__ import annotations

import os
import uuid

import pytest

from app.graph.checkpointer import prune_checkpoints


@pytest.fixture()
def pg_required():
    if os.getenv("COMPLAINT_DB_TESTS") != "1":
        pytest.skip("COMPLAINT_DB_TESTS != 1，跳过需要真实 PG 的裁剪用例")


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
def created_threads():
    """记录用例创建的 thread_id，供清理。"""
    return []


@pytest.fixture()
async def make_thread(pg_conn, created_threads):
    """建一条「case + complaint + session + checkpoint」完整线程。

    status: 会话状态；age_days: 把 session.update_time 回拨的天数。
    返回 thread_id（= complaint_id）。
    """
    async def _make(status: str, age_days: int) -> str:
        cur = await pg_conn.execute(
            "INSERT INTO cases (id, case_number, customer_name, phone_encrypted, "
            "phone_hash, overdue_amount, overdue_days, account_stage) "
            "VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (f"IT-{uuid.uuid4().hex[:12]}", "裁剪测试", "enc", "hash", 1, 1, "M1"),
        )
        case_id = (await cur.fetchone())[0]

        cur = await pg_conn.execute(
            "INSERT INTO complaints (id, case_id, complaint_text) "
            "VALUES (%s, %s, %s) RETURNING id",
            (case_id, case_id, "裁剪用例"),
        )
        cid = (await cur.fetchone())[0]

        await pg_conn.execute(
            "INSERT INTO complaint_sessions "
            "(session_id, trace_id, complaint_id, complaint_text, task_status, update_time) "
            "VALUES (%s, %s, %s, %s, %s, now() - make_interval(days => %s))",
            (f"s-{cid}", f"t-{cid}", cid, "裁剪用例", status, age_days),
        )

        tid = str(cid)
        for table, cols in (
            ("checkpoints",
             "(thread_id, checkpoint_ns, checkpoint_id, checkpoint, metadata) "
             "VALUES (%s, '', 'cp-1', '{}'::jsonb, '{}'::jsonb)"),
            ("checkpoint_blobs",
             "(thread_id, checkpoint_ns, channel, version, type, blob) "
             "VALUES (%s, '', 'ch', 'v1', 'json', NULL)"),
            ("checkpoint_writes",
             "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, blob, task_path) "
             "VALUES (%s, '', 'cp-1', 'tk', 0, 'ch', ''::bytea, '')"),
        ):
            await pg_conn.execute(f"INSERT INTO {table} {cols}", (tid,))

        created_threads.append((tid, str(case_id)))
        return tid

    return _make


@pytest.fixture(autouse=True)
async def _cleanup(pg_conn, created_threads):
    yield
    for tid, case_id in created_threads:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await pg_conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (tid,))
        await pg_conn.execute("DELETE FROM cases WHERE id = %s", (case_id,))


async def _row_counts(pg_conn, tid: str) -> dict:
    out = {}
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        cur = await pg_conn.execute(
            f"SELECT count(*) FROM {table} WHERE thread_id = %s", (tid,)
        )
        out[table] = (await cur.fetchone())[0]
    return out


async def test_never_prunes_non_terminal_threads(make_thread, pg_conn):
    """**安全断言**：待人工复核等非终态线程，一个 checkpoint 都不能删。

    pending_review / pending_senior_review 正在等人工动作，删其 checkpoint
    等于丢掉 resume 能力。

    刻意在同一次裁剪里放一条**终态**线程作为对照：否则「没被删」可能只是
    因为裁剪整个没工作（假通过）。对照组必须被删掉，才证明是选择性生效。
    """
    protected = {}
    for status in ("pending_review", "pending_senior_review", "judging",
                   "evidence_gathering", "draft_generated"):
        protected[await make_thread(status, age_days=365)] = status

    control = await make_thread("closed", age_days=365)

    candidates = (await prune_checkpoints(90, dry_run=True))["threads"]
    assert control in candidates, "对照组未被识别 → 裁剪逻辑可能整体失效"
    for tid in protected:
        assert tid not in candidates, "非终态线程被误列为可裁剪"

    await prune_checkpoints(90, dry_run=False)

    # 对照组必须被清空，证明裁剪真的生效
    assert all(v == 0 for v in (await _row_counts(pg_conn, control)).values()), \
        "对照组未被裁剪（裁剪未生效，本用例的其余断言无意义）"
    # 非终态线程必须完好
    for tid, status in protected.items():
        counts = await _row_counts(pg_conn, tid)
        assert all(v == 1 for v in counts.values()), f"{status} 线程被误删: {counts}"


async def test_prunes_terminal_threads_beyond_retention(make_thread, pg_conn):
    """终态且超期的线程：三表全清。"""
    tid = await make_thread("closed", age_days=120)
    assert tid in (await prune_checkpoints(90, dry_run=True))["threads"]

    await prune_checkpoints(90, dry_run=False)
    assert all(v == 0 for v in (await _row_counts(pg_conn, tid)).values())


async def test_respects_retention_window(make_thread, pg_conn):
    """终态但未超期（update_time 在保留窗口内）→ 不裁剪。"""
    tid = await make_thread("resolved", age_days=10)
    assert tid not in (await prune_checkpoints(90, dry_run=True))["threads"]

    await prune_checkpoints(90, dry_run=False)
    assert all(v == 1 for v in (await _row_counts(pg_conn, tid)).values())


async def test_dry_run_deletes_nothing(make_thread, pg_conn):
    """dry_run 只报告，不删任何数据。"""
    tid = await make_thread("archived", age_days=200)
    stats = await prune_checkpoints(90, dry_run=True)
    assert tid in stats["threads"]
    assert stats["deleted"] == {}
    assert all(v == 1 for v in (await _row_counts(pg_conn, tid)).values())


async def test_non_positive_retention_is_refused(make_thread, pg_conn):
    """retention_days <= 0 拒绝执行（防「保留 0 天」误删全部）。"""
    tid = await make_thread("closed", age_days=400)
    stats = await prune_checkpoints(0, dry_run=False)
    assert stats["threads"] == []
    assert all(v == 1 for v in (await _row_counts(pg_conn, tid)).values())


async def test_prune_reports_counts(make_thread):
    """返回值含被删行数，便于运维核对。"""
    tid = await make_thread("closed", age_days=150)
    stats = await prune_checkpoints(90, dry_run=False)
    assert tid in stats["threads"]
    assert stats["deleted"]["checkpoints"] >= 1
    assert stats["deleted"]["checkpoint_blobs"] >= 1
    assert stats["deleted"]["checkpoint_writes"] >= 1

"""checkpointer PG 持久化验收测试。

需真实 PG：`COMPLAINT_DB_TESTS=1` + `COMPLAINT_PG_DSN` 指向测试库。
对齐 rag-service 的 `RAG_DB_TESTS` 约定（services/rag-service/tests/conftest.py）。

本组是「断点跨进程存活」的唯一真证据——mock 层面的通过不代表真库可用
（recovery_service 的 MagicMock 掩盖签名错误就是教训）。

全部用例写成 async：psycopg 的 AsyncConnection 绑定创建它的 event loop，
在 sync 测试里用 `asyncio.run()` 会跨 loop 使用连接而报错。
"""
from __future__ import annotations

import os
import uuid

import pytest
from langgraph.types import Command

from app.graph.builder import build_complaint_graph
from app.models.state import ComplaintSessionState, TaskStatus


@pytest.fixture()
def pg_required():
    if os.getenv("COMPLAINT_DB_TESTS") != "1":
        pytest.skip("COMPLAINT_DB_TESTS != 1，跳过需要真实 PG 的 checkpointer 用例")


@pytest.fixture()
def dsn() -> str:
    """测试库 DSN，由 PG_* 设置派生（不依赖全局 COMPLAINT_PG_DSN）。

    刻意不把 COMPLAINT_PG_DSN 设成全局环境变量：它一设上，每个构造图的用例
    都会去建真实 PG 连接池（conftest 每用例重置 checkpointer 单例），整个套件会
    从 ~16s 退化到分钟级。只在需要它的用例内用 monkeypatch 局部设置。
    """
    from app.config import settings

    return settings.pg_dsn_psycopg3


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
async def thread_id(pg_conn):
    tid = f"ckpt-test-{uuid.uuid4()}"
    try:
        yield tid
    finally:
        for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
            await pg_conn.execute(f"DELETE FROM {table} WHERE thread_id = %s", (tid,))


def _state(cid: str) -> dict:
    return ComplaintSessionState(
        session_id=f"s-{cid}",
        trace_id=f"t-{cid}",
        complaint_id=cid,
        complaint_text="用户投诉催收",
        case_ids=["case-1"],
    ).model_dump()


async def test_schema_has_task_path_and_nullable_blob(pg_conn):
    """锁死两个「漏了就静默坏掉」的列。

    - checkpoint_writes.task_path：SELECT_PENDING_SENDS_SQL 按它排序，
      缺列会让 Command(resume=...) 报错并被 _resume_graph_thread 吞掉 → resume 静默失败。
    - checkpoint_blobs.blob 必须可空（官方 MIGRATIONS 第 4 条专门 DROP NOT NULL）。
    """
    cur = await pg_conn.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'checkpoint_writes' AND column_name = 'task_path'"
    )
    row = await cur.fetchone()
    assert row is not None, "checkpoint_writes.task_path 缺失 —— resume 会静默失败"

    cur = await pg_conn.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'checkpoint_blobs' AND column_name = 'blob'"
    )
    row = await cur.fetchone()
    assert row is not None, "checkpoint_blobs.blob 缺失"
    assert row[0] == "YES", "checkpoint_blobs.blob 必须可空"


async def test_interrupt_survives_new_saver_instance(thread_id, dsn, monkeypatch):
    """全案核心断言：断点在**全新 saver 实例**上依然可恢复（模拟服务重启）。

    步骤：saver1 跑图到 human_review 断点 → 丢弃 saver1 与整个 graph 对象
    → saver2（新连接池）编译新图 → 断点仍在 → Command(resume) 恢复成功。
    """
    from app.graph import checkpointer as ck

    monkeypatch.setenv("COMPLAINT_PG_DSN", dsn)
    cfg = {"configurable": {"thread_id": thread_id}}

    # ── 第一段：saver1 跑到断点 ──
    ck.reset_checkpointer_for_tests()
    saver1 = await ck.get_checkpointer()
    graph1 = build_complaint_graph(checkpointer=saver1)
    out = await graph1.ainvoke(_state(thread_id), cfg)
    assert "__interrupt__" in out, "图未停在 human_review 断点"

    # ── 模拟重启：丢弃 saver1 与图对象，重建连接池 ──
    await ck.shutdown_checkpointer()
    ck.reset_checkpointer_for_tests()

    saver2 = await ck.get_checkpointer()
    assert saver2 is not saver1, "应为全新 saver 实例"
    graph2 = build_complaint_graph(checkpointer=saver2)

    snap = await graph2.aget_state(cfg)
    assert tuple(snap.next) == ("human_review",), "重启后断点丢失"

    # ── 恢复 ──
    final = await graph2.ainvoke(Command(resume="approve"), cfg)
    assert final["task_status"] == TaskStatus.resolved.value
    assert tuple((await graph2.aget_state(cfg)).next) == ()

    await ck.shutdown_checkpointer()

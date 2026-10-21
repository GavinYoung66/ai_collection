"""LangGraph checkpoint 三表 + complaint_sessions.task_status 补 pending_callback。

DDL 来源：langgraph/checkpoint/postgres/base.py 的 MIGRATIONS 列表（逐字对照，
已展开为「最终态」），使表结构与 AsyncPostgresSaver 的期望完全一致。

三点刻意的取舍：
1. **手写而非调用 saver.setup()**：schema 归 alembic 所有，可评审、可回滚，
   且多副本启动时不会争相建表。
2. **不建 checkpoint_migrations 表**：运行时不读它（只在 setup() 内使用）。
   保持缺席可让未来 langgraph 升级带来的新迁移自然收敛；若在此写入版本号，
   反而锁死后续升级路径。
3. **索引不用 CONCURRENTLY**：原 DDL 的三条索引是 CREATE INDEX CONCURRENTLY，
   而该语句不能在事务块内执行，alembic 默认把 upgrade() 包在事务里 → 必然失败。
   三表是本次新建的空表，普通 CREATE INDEX 无锁等待问题。

两个「漏了就静默坏掉」的列（务必保留）：
- checkpoint_writes.task_path：SELECT_PENDING_SENDS_SQL 按它排序，缺列会让
  Command(resume=...) 静默失败（异常被 _resume_graph_thread 吞掉）。
- checkpoint_blobs.blob 可空：官方 MIGRATIONS 第 4 条专门 DROP NOT NULL。

另修 complaint_sessions.task_status 的 CHECK 约束：原 0010 的约束与 ORM 枚举
都缺 'pending_callback'，而 complaint_service 的 V4 定责流程会写这个状态，
不修则 session 写穿恰好在定责路径撞约束失败。

Revision ID: 0015
Revises: 0014
Create Date: 2026-09-30

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0015"
down_revision: Union[str, None] = "0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# task_status 合法值，与 app/models/state.py 的 TaskStatus 逐字一致（11 值）
_TASK_STATUSES_11 = (
    "pending", "evidence_gathering", "reflection_retry", "judging",
    "draft_generated", "pending_review", "pending_senior_review", "pending_callback",
    "resolved", "closed", "archived",
)
# 0010 的原始 10 值版本（downgrade 用）
_TASK_STATUSES_10 = tuple(s for s in _TASK_STATUSES_11 if s != "pending_callback")


def _status_in_clause(values: tuple) -> str:
    return ",".join(repr(v) for v in values)


def upgrade() -> None:
    # ============================================================
    # 一、LangGraph checkpoint 三表
    # ============================================================
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            thread_id            TEXT NOT NULL,
            checkpoint_ns        TEXT NOT NULL DEFAULT '',
            checkpoint_id        TEXT NOT NULL,
            parent_checkpoint_id TEXT,
            type                 TEXT,
            checkpoint           JSONB NOT NULL,
            metadata             JSONB NOT NULL DEFAULT '{}',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_blobs (
            thread_id     TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            channel       TEXT NOT NULL,
            version       TEXT NOT NULL,
            type          TEXT NOT NULL,
            blob          BYTEA,
            PRIMARY KEY (thread_id, checkpoint_ns, channel, version)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoint_writes (
            thread_id     TEXT NOT NULL,
            checkpoint_ns TEXT NOT NULL DEFAULT '',
            checkpoint_id TEXT NOT NULL,
            task_id       TEXT NOT NULL,
            idx           INTEGER NOT NULL,
            channel       TEXT NOT NULL,
            type          TEXT,
            blob          BYTEA NOT NULL,
            task_path     TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx)
        );
        """
    )

    # 索引：非并发（见文件头取舍 3）；与官方索引名保持一致，便于未来 setup() 收敛
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx "
        "ON checkpoints(thread_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx "
        "ON checkpoint_blobs(thread_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx "
        "ON checkpoint_writes(thread_id);"
    )

    # 列注释：说明这两张表的写入方与保留策略
    op.execute(
        "COMMENT ON TABLE checkpoints IS "
        "'LangGraph 图检查点（thread_id = complaint_id）。由 AsyncPostgresSaver 写入，'"
        "'业务代码只读。无界增长治理见 checkpointer.archive_expired_sessions'"
    )
    op.execute(
        "COMMENT ON TABLE checkpoint_blobs IS "
        "'LangGraph channel 值快照（BYTEA 序列化全量 state）。本表是增长主体'"
    )
    op.execute(
        "COMMENT ON TABLE checkpoint_writes IS "
        "'LangGraph 待写记录。task_path 列缺失会导致 resume 静默失败'"
    )

    # ============================================================
    # 二、complaint_sessions.task_status 补 pending_callback
    # ============================================================
    # 动态删除既有 task_status CHECK（不硬编 PG 自动生成的名字，抗未来变动）
    op.execute(
        """
        DO $$
        DECLARE
            c text;
        BEGIN
            IF to_regclass('public.complaint_sessions') IS NULL THEN
                RAISE NOTICE 'complaint_sessions 不存在，跳过 task_status 约束修正';
                RETURN;
            END IF;
            FOR c IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'public.complaint_sessions'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%task_status%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE public.complaint_sessions DROP CONSTRAINT %I', c
                );
            END LOOP;
        END $$;
        """
    )
    # 重建为 11 值（含 pending_callback），与 app/models/state.py:TaskStatus 对齐
    op.execute(
        f"""
        DO $$
        BEGIN
            IF to_regclass('public.complaint_sessions') IS NULL THEN
                RETURN;
            END IF;
            ALTER TABLE public.complaint_sessions
                ADD CONSTRAINT complaint_sessions_task_status_check
                CHECK (task_status IN ({_status_in_clause(_TASK_STATUSES_11)}));
        END $$;
        """
    )


def downgrade() -> None:
    # === 二、task_status 约束回退到 10 值 ===
    op.execute(
        f"""
        DO $$
        DECLARE
            c text;
        BEGIN
            IF to_regclass('public.complaint_sessions') IS NULL THEN
                RETURN;
            END IF;
            FOR c IN
                SELECT conname FROM pg_constraint
                WHERE conrelid = 'public.complaint_sessions'::regclass
                  AND contype = 'c'
                  AND pg_get_constraintdef(oid) LIKE '%task_status%'
            LOOP
                EXECUTE format(
                    'ALTER TABLE public.complaint_sessions DROP CONSTRAINT %I', c
                );
            END LOOP;
            ALTER TABLE public.complaint_sessions
                ADD CONSTRAINT complaint_sessions_task_status_check
                CHECK (task_status IN ({_status_in_clause(_TASK_STATUSES_10)}));
        END $$;
        """
    )

    # === 一、checkpoint 三表 DROP（无 FK 依赖，顺序仅为可读性）===
    op.execute("DROP TABLE IF EXISTS checkpoint_writes")
    op.execute("DROP TABLE IF EXISTS checkpoint_blobs")
    op.execute("DROP TABLE IF EXISTS checkpoints")

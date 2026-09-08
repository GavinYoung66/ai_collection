"""complaint-service 数据层：complaint_sessions + complaint_agent_logs 建表 + complaints 扩展

- CREATE TABLE complaint_sessions（PRD 9.1）
- CREATE TABLE complaint_agent_logs（PRD 9.1，append-only）
- complaints ALTER：新增 6 字段（PRD 9.2）+ verdict CHECK 改为四值（去 normal）
- 索引对齐 PRD 9.1

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-05

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0010"
down_revision: Union[str, None] = "0009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ============================================================
    # complaint_sessions
    # ============================================================
    op.execute(
        """
        CREATE TABLE complaint_sessions (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id VARCHAR(100) UNIQUE NOT NULL,
            trace_id VARCHAR(100) NOT NULL,
            complaint_id UUID NOT NULL REFERENCES complaints(id) ON DELETE CASCADE,
            case_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            session_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
            user_id VARCHAR(100),
            complaint_text TEXT NOT NULL,
            loop_count INT NOT NULL DEFAULT 0 CHECK (loop_count BETWEEN 0 AND 3),
            max_loops INT NOT NULL DEFAULT 3,
            case_evidence JSONB DEFAULT '{}'::jsonb,
            cross_case_evidence JSONB DEFAULT '[]'::jsonb,
            historical_complaint_evidence JSONB DEFAULT '[]'::jsonb,
            reflection_report JSONB,
            fact_timeline JSONB,
            compliance_opinion JSONB,
            user_demand JSONB,
            verdict_draft JSONB,
            judgment_package JSONB,
            exception_info JSONB DEFAULT '{}'::jsonb,
            human_review JSONB,
            task_status VARCHAR(30) NOT NULL DEFAULT 'pending' CHECK (
                task_status IN ('pending','evidence_gathering','reflection_retry','judging',
                                'draft_generated','pending_review','pending_senior_review',
                                'resolved','closed','archived')
            ),
            total_tokens_consumed INT DEFAULT 0,
            max_token_threshold INT DEFAULT 50000,
            mode VARCHAR(20) NOT NULL DEFAULT 'multi_agent' CHECK (mode IN ('single_agent','multi_agent')),
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
            is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
        );
        """
    )
    op.execute("CREATE INDEX idx_complaint_sessions_session_id ON complaint_sessions(session_id);")
    op.execute("CREATE INDEX idx_complaint_sessions_complaint_id ON complaint_sessions(complaint_id);")
    op.execute("CREATE INDEX idx_complaint_sessions_task_status ON complaint_sessions(task_status);")
    op.execute("CREATE INDEX idx_complaint_sessions_trace_id ON complaint_sessions(trace_id);")
    op.execute(
        "CREATE INDEX idx_complaint_sessions_active ON complaint_sessions(task_status) "
        "WHERE task_status NOT IN ('archived','closed') AND is_delete = '0';"
    )

    # ============================================================
    # complaint_agent_logs（append-only）
    # ============================================================
    op.execute(
        """
        CREATE TABLE complaint_agent_logs (
            id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
            session_id VARCHAR(100) NOT NULL REFERENCES complaint_sessions(session_id) ON DELETE CASCADE,
            trace_id VARCHAR(100) NOT NULL,
            agent_role VARCHAR(50) NOT NULL CHECK (agent_role IN (
                'orchestrator','evidence_case','evidence_call_qc','reflection',
                'fact','compliance','user_demand','verdict','generate_package','sample_backflow'
            )),
            step_name VARCHAR(100) NOT NULL,
            input_summary TEXT,
            output_json JSONB,
            mcp_calls JSONB DEFAULT '[]'::jsonb,
            llm_call_log_ids JSONB DEFAULT '[]'::jsonb,
            tokens_consumed INT DEFAULT 0,
            latency_ms INT,
            status VARCHAR(20) NOT NULL CHECK (status IN ('success','failed','degraded','skipped')),
            error_msg TEXT,
            create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """
    )
    op.execute("CREATE INDEX idx_complaint_agent_logs_session_id ON complaint_agent_logs(session_id);")
    op.execute("CREATE INDEX idx_complaint_agent_logs_agent_role ON complaint_agent_logs(agent_role);")
    op.execute("CREATE INDEX idx_complaint_agent_logs_create_time ON complaint_agent_logs(create_time DESC);")
    op.execute("CREATE INDEX idx_complaint_agent_logs_trace_id ON complaint_agent_logs(trace_id);")

    # ============================================================
    # complaints 扩展字段（PRD 9.2）
    # ============================================================
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS verdict_reasoning TEXT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS response_draft TEXT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS regulation_refs JSONB DEFAULT '[]'::jsonb;")
    op.execute(
        "ALTER TABLE complaints ADD COLUMN IF NOT EXISTS confidence FLOAT "
        "CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));"
    )
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS internal_improvement TEXT;")
    op.execute("ALTER TABLE complaints ADD COLUMN IF NOT EXISTS conflict_highlight BOOLEAN DEFAULT FALSE;")

    # verdict CHECK 改为四值（去掉 normal）
    op.execute("ALTER TABLE complaints DROP CONSTRAINT IF EXISTS complaints_verdict_check;")
    op.execute(
        "ALTER TABLE complaints ADD CONSTRAINT complaints_verdict_check CHECK ("
        "verdict IN ('agent_violation','user_misunderstanding','compliant_but_poor_experience','pending'));"
    )


def downgrade() -> None:
    # complaints 回滚：移除扩展字段 + 恢复 verdict CHECK
    op.execute("ALTER TABLE complaints DROP CONSTRAINT IF EXISTS complaints_verdict_check;")
    op.execute(
        "ALTER TABLE complaints ADD CONSTRAINT complaints_verdict_check CHECK ("
        "verdict IN ('agent_violation','user_misunderstanding','normal','pending'));"
    )
    op.execute("ALTER TABLE complaints DROP COLUMN IF EXISTS conflict_highlight;")
    op.execute("ALTER TABLE complaints DROP COLUMN IF EXISTS internal_improvement;")
    op.execute("ALTER TABLE complaints DROP COLUMN IF EXISTS confidence;")
    op.execute("ALTER TABLE complaints DROP COLUMN IF EXISTS regulation_refs;")
    op.execute("ALTER TABLE complaints DROP COLUMN IF EXISTS response_draft;")
    op.execute("ALTER TABLE complaints DROP COLUMN IF EXISTS verdict_reasoning;")

    # 删表
    op.execute("DROP TABLE IF EXISTS complaint_agent_logs;")
    op.execute("DROP TABLE IF EXISTS complaint_sessions;")

"""init: 19 business tables + 8 dw tables

Revision ID: 0001
Revises:
Create Date: 2026-08-20

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 扩展 ===
    op.execute('CREATE EXTENSION IF NOT EXISTS "uuid-ossp";')

    # === 1. cases ===
    op.execute("""
    CREATE TABLE cases (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        case_number VARCHAR(50) UNIQUE NOT NULL,
        customer_name VARCHAR(100) NOT NULL,
        phone_encrypted VARCHAR(255) NOT NULL,
        phone_hash VARCHAR(64) NOT NULL,
        id_card_hash VARCHAR(64),
        overdue_amount DECIMAL(12,2) NOT NULL,
        overdue_days INT NOT NULL,
        account_stage VARCHAR(10) NOT NULL CHECK (account_stage IN ('M0','M1','M2','M3')),
        case_status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (case_status IN ('pending','processing','paid','bad_debt','lost')),
        assigned_to UUID,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_cases_case_number ON cases(case_number);")
    op.execute("CREATE INDEX idx_cases_phone_hash ON cases(phone_hash);")
    op.execute("CREATE INDEX idx_cases_account_stage ON cases(account_stage);")
    op.execute("CREATE INDEX idx_cases_case_status ON cases(case_status);")
    op.execute("CREATE INDEX idx_cases_assigned_to ON cases(assigned_to);")

    # === 2. users ===
    op.execute("""
    CREATE TABLE users (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        username VARCHAR(50) UNIQUE NOT NULL,
        password_hash VARCHAR(255) NOT NULL,
        real_name VARCHAR(50) NOT NULL,
        role VARCHAR(20) NOT NULL CHECK (role IN ('admin','supervisor','agent','qc_reviewer','ai_trainer')),
        agent_skills JSONB DEFAULT '[]'::jsonb,
        status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled','locked')),
        last_login_at TIMESTAMP WITH TIME ZONE,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_users_username ON users(username);")
    op.execute("CREATE INDEX idx_users_role ON users(role);")
    op.execute("CREATE INDEX idx_users_status ON users(status);")

    # === 3. call_records ===
    op.execute("""
    CREATE TABLE call_records (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id VARCHAR(100) UNIQUE NOT NULL,
        trace_id VARCHAR(100),
        case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        agent_id UUID REFERENCES users(id) ON DELETE SET NULL,
        call_type VARCHAR(20) NOT NULL CHECK (call_type IN ('ai','human')),
        call_status VARCHAR(20) NOT NULL CHECK (call_status IN ('ringing','connected','no_answer','failed','completed')),
        start_time TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
        end_time TIMESTAMP WITH TIME ZONE,
        duration_seconds INT DEFAULT 0,
        result VARCHAR(20) CHECK (result IN ('ptp','refused','no_answer','lost','escalated','completed')),
        sentiment_score FLOAT CHECK (sentiment_score IS NULL OR (sentiment_score >= 0 AND sentiment_score <= 1)),
        record_url TEXT,
        summary TEXT,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_call_records_session_id ON call_records(session_id);")
    op.execute("CREATE INDEX idx_call_records_case_id ON call_records(case_id);")
    op.execute("CREATE INDEX idx_call_records_agent_id ON call_records(agent_id);")
    op.execute("CREATE INDEX idx_call_records_start_time ON call_records(start_time DESC);")

    # === 4. dialog_turns (append-only) ===
    op.execute("""
    CREATE TABLE dialog_turns (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id VARCHAR(100) NOT NULL REFERENCES call_records(session_id) ON DELETE CASCADE,
        turn_number INT NOT NULL,
        speaker VARCHAR(10) NOT NULL CHECK (speaker IN ('ai','user')),
        text TEXT NOT NULL,
        reasoning_encrypted TEXT,
        confidence FLOAT CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
        intent_label VARCHAR(30),
        latency_ms INT,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_dialog_turns_session_id ON dialog_turns(session_id);")
    op.execute("CREATE INDEX idx_dialog_turns_create_time ON dialog_turns(create_time DESC);")

    # === 5. qc_records ===
    op.execute("""
    CREATE TABLE qc_records (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id VARCHAR(100) NOT NULL REFERENCES call_records(session_id) ON DELETE CASCADE,
        compliance_score INT NOT NULL CHECK (compliance_score BETWEEN 0 AND 100),
        risk_level VARCHAR(10) NOT NULL CHECK (risk_level IN ('low','medium','high')),
        qc_result VARCHAR(20) NOT NULL CHECK (qc_result IN ('compliant','violation','pending_review')),
        violations JSONB,
        reviewer_id UUID REFERENCES users(id) ON DELETE SET NULL,
        review_comment TEXT,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_qc_records_session_id ON qc_records(session_id);")
    op.execute("CREATE INDEX idx_qc_records_qc_result ON qc_records(qc_result);")

    # === 6. intent_tags (append-only) ===
    op.execute("""
    CREATE TABLE intent_tags (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id VARCHAR(100) NOT NULL REFERENCES call_records(session_id) ON DELETE CASCADE,
        turn_number INT,
        intent_type VARCHAR(30) NOT NULL CHECK (intent_type IN ('willing_to_pay','extension_request','financial_difficulty','stalling','rejection','anger','complaint','uncertain')),
        confidence FLOAT NOT NULL CHECK (confidence BETWEEN 0 AND 1),
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_intent_tags_session_id ON intent_tags(session_id);")

    # === 7. kb_documents ===
    op.execute("""
    CREATE TABLE kb_documents (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        title VARCHAR(255) NOT NULL,
        category VARCHAR(50) NOT NULL CHECK (category IN ('regulation','script','case','other')),
        content TEXT NOT NULL,
        version INT DEFAULT 1,
        status VARCHAR(20) DEFAULT 'active' CHECK (status IN ('active','archived')),
        created_by UUID REFERENCES users(id) ON DELETE SET NULL,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_kb_documents_category ON kb_documents(category);")

    # === 8. kb_chunks ===
    op.execute("""
    CREATE TABLE kb_chunks (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        doc_id UUID NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
        chunk_index INT NOT NULL,
        content TEXT NOT NULL,
        vector_id VARCHAR(100) NOT NULL,
        category VARCHAR(50),
        version INT DEFAULT 1,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1')),
        UNIQUE(doc_id, chunk_index)
    );
    """)
    op.execute("CREATE INDEX idx_kb_chunks_doc_id ON kb_chunks(doc_id);")
    op.execute("CREATE INDEX idx_kb_chunks_category ON kb_chunks(category);")
    op.execute("CREATE UNIQUE INDEX idx_kb_chunks_vector_id ON kb_chunks(vector_id);")

    # === 9. sensitive_words ===
    op.execute("""
    CREATE TABLE sensitive_words (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        word VARCHAR(100) NOT NULL UNIQUE,
        category VARCHAR(20) NOT NULL CHECK (category IN ('threat','abuse','induce')),
        severity VARCHAR(10) NOT NULL CHECK (severity IN ('high','medium','low')),
        status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
        description VARCHAR(255),
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_sensitive_words_category ON sensitive_words(category);")
    op.execute("CREATE INDEX idx_sensitive_words_severity ON sensitive_words(severity);")

    # === 10. qc_rules ===
    op.execute("""
    CREATE TABLE qc_rules (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        rule_type VARCHAR(30) NOT NULL CHECK (rule_type IN ('keyword','regex','semantic','duration','silence')),
        pattern TEXT NOT NULL,
        severity VARCHAR(10) NOT NULL CHECK (severity IN ('high','medium','low')),
        score_deduct INT NOT NULL DEFAULT 10 CHECK (score_deduct BETWEEN 0 AND 100),
        status VARCHAR(20) NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
        description VARCHAR(255),
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_qc_rules_rule_type ON qc_rules(rule_type);")
    op.execute("CREATE INDEX idx_qc_rules_severity ON qc_rules(severity);")

    # === 11. assignment_strategies ===
    op.execute("""
    CREATE TABLE assignment_strategies (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(100) NOT NULL,
        rules JSONB NOT NULL,
        version INT DEFAULT 1,
        status VARCHAR(20) DEFAULT 'draft' CHECK (status IN ('draft','active','archived')),
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_assignment_strategies_status ON assignment_strategies(status);")

    # === 12. call_strategies ===
    op.execute("""
    CREATE TABLE call_strategies (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        name VARCHAR(100) NOT NULL,
        rules JSONB NOT NULL,
        version INT DEFAULT 1,
        status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','archived')),
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_call_strategies_status ON call_strategies(status);")
    op.execute("CREATE INDEX idx_call_strategies_name ON call_strategies(name);")

    # === 13. strategy_versions ===
    op.execute("""
    CREATE TABLE strategy_versions (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        strategy_type VARCHAR(30) NOT NULL CHECK (strategy_type IN ('assignment','qc_scoring','call_flow','complaint_handling','review_classify')),
        params JSONB NOT NULL,
        version INT NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','active','archived')),
        approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1')),
        UNIQUE(strategy_type, version)
    );
    """)
    op.execute("CREATE INDEX idx_strategy_versions_strategy_type ON strategy_versions(strategy_type);")
    op.execute("CREATE INDEX idx_strategy_versions_status ON strategy_versions(status);")

    # === 14. features_user (dw 例外) ===
    op.execute("""
    CREATE TABLE features_user (
        case_id UUID PRIMARY KEY REFERENCES cases(id) ON DELETE CASCADE,
        willingness_score INT CHECK (willingness_score BETWEEN 0 AND 100),
        ability_score INT CHECK (ability_score BETWEEN 0 AND 100),
        complaint_risk_score INT CHECK (complaint_risk_score BETWEEN 0 AND 100),
        overall_risk_level VARCHAR(10) CHECK (overall_risk_level IN ('low','medium','high')),
        features JSONB DEFAULT '{}'::jsonb,
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_features_user_overall_risk_level ON features_user(overall_risk_level);")

    # === 15. complaints ===
    op.execute("""
    CREATE TABLE complaints (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        case_id UUID NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
        session_id VARCHAR(100) REFERENCES call_records(session_id) ON DELETE CASCADE,
        complaint_text TEXT NOT NULL,
        verdict VARCHAR(30) CHECK (verdict IN ('agent_violation','user_misunderstanding','normal','pending')),
        evidence JSONB,
        suggestion TEXT,
        status VARCHAR(20) NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','investigating','resolved','closed')),
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_complaints_case_id ON complaints(case_id);")
    op.execute("CREATE INDEX idx_complaints_session_id ON complaints(session_id);")
    op.execute("CREATE INDEX idx_complaints_status ON complaints(status);")

    # === 16. review_cases ===
    op.execute("""
    CREATE TABLE review_cases (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        session_id VARCHAR(100) NOT NULL REFERENCES call_records(session_id) ON DELETE CASCADE,
        category VARCHAR(20) NOT NULL CHECK (category IN ('good','bad','violation','ineffective')),
        score INT CHECK (score BETWEEN 0 AND 100),
        scripts JSONB DEFAULT '[]'::jsonb,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        update_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
    );
    """)
    op.execute("CREATE INDEX idx_review_cases_session_id ON review_cases(session_id);")
    op.execute("CREATE INDEX idx_review_cases_category ON review_cases(category);")
    op.execute("CREATE INDEX idx_review_cases_score ON review_cases(score DESC);")

    # === 17. llm_call_logs (append-only) ===
    op.execute("""
    CREATE TABLE llm_call_logs (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        trace_id VARCHAR(100) NOT NULL,
        session_id VARCHAR(100),
        provider VARCHAR(50) NOT NULL,
        model VARCHAR(50),
        prompt TEXT NOT NULL,
        response TEXT,
        token_usage JSONB,
        latency_ms INT,
        confidence FLOAT CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
        temperature FLOAT DEFAULT 0.1,
        status VARCHAR(20) NOT NULL CHECK (status IN ('success','failed','timeout','circuit_open')),
        error_msg TEXT,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_llm_call_logs_trace_id ON llm_call_logs(trace_id);")
    op.execute("CREATE INDEX idx_llm_call_logs_session_id ON llm_call_logs(session_id);")
    op.execute("CREATE INDEX idx_llm_call_logs_status ON llm_call_logs(status);")
    op.execute("CREATE INDEX idx_llm_call_logs_create_time ON llm_call_logs(create_time DESC);")

    # === 18. audit_logs (append-only) ===
    op.execute("""
    CREATE TABLE audit_logs (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        trace_id VARCHAR(100),
        user_id UUID NOT NULL REFERENCES users(id) ON DELETE SET NULL,
        action VARCHAR(50) NOT NULL,
        resource_type VARCHAR(50),
        resource_id VARCHAR(100),
        detail JSONB,
        ip_address INET,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_audit_logs_user_id ON audit_logs(user_id);")
    op.execute("CREATE INDEX idx_audit_logs_create_time ON audit_logs(create_time DESC);")
    op.execute("CREATE INDEX idx_audit_logs_trace_id ON audit_logs(trace_id);")

    # === 19. etl_task_logs (append-only) ===
    op.execute("""
    CREATE TABLE etl_task_logs (
        id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
        task_name VARCHAR(100) NOT NULL,
        status VARCHAR(20) NOT NULL CHECK (status IN ('running','success','failed','skipped')),
        start_time TIMESTAMP WITH TIME ZONE NOT NULL,
        end_time TIMESTAMP WITH TIME ZONE,
        duration_ms INT,
        records_processed INT DEFAULT 0,
        error_msg TEXT,
        create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_etl_task_logs_task_name ON etl_task_logs(task_name);")
    op.execute("CREATE INDEX idx_etl_task_logs_status ON etl_task_logs(status);")

    # === dw schema ===
    op.execute("CREATE SCHEMA IF NOT EXISTS dw;")

    # === dw.ods_call_records ===
    op.execute("""
    CREATE TABLE dw.ods_call_records (
        id UUID NOT NULL,
        session_id VARCHAR(100) NOT NULL,
        trace_id VARCHAR(100),
        case_id UUID NOT NULL,
        agent_id UUID,
        call_type VARCHAR(20) NOT NULL,
        call_status VARCHAR(20) NOT NULL,
        start_time TIMESTAMP WITH TIME ZONE NOT NULL,
        end_time TIMESTAMP WITH TIME ZONE,
        duration_seconds INT DEFAULT 0,
        result VARCHAR(20),
        sentiment_score FLOAT,
        record_url TEXT,
        summary TEXT,
        create_time TIMESTAMP WITH TIME ZONE,
        _synced_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_ods_call_records_session_id ON dw.ods_call_records(session_id);")
    op.execute("CREATE INDEX idx_ods_call_records_case_id ON dw.ods_call_records(case_id);")

    # === dw.dwd_call_records ===
    op.execute("""
    CREATE TABLE dw.dwd_call_records (
        session_id VARCHAR(100) PRIMARY KEY,
        case_id UUID NOT NULL,
        agent_id UUID,
        call_type VARCHAR(20) NOT NULL CHECK (call_type IN ('ai','human')),
        call_status VARCHAR(20) NOT NULL CHECK (call_status IN ('ringing','connected','no_answer','failed','completed')),
        start_time TIMESTAMP WITH TIME ZONE NOT NULL,
        end_time TIMESTAMP WITH TIME ZONE,
        duration_seconds INT NOT NULL DEFAULT 0 CHECK (duration_seconds >= 0),
        result VARCHAR(20) CHECK (result IN ('ptp','refused','no_answer','lost','escalated','completed')),
        sentiment_score FLOAT CHECK (sentiment_score IS NULL OR (sentiment_score >= 0 AND sentiment_score <= 1)),
        record_url TEXT,
        summary TEXT,
        etl_date DATE NOT NULL,
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_dwd_call_records_case_id ON dw.dwd_call_records(case_id);")
    op.execute("CREATE INDEX idx_dwd_call_records_etl_date ON dw.dwd_call_records(etl_date);")

    # === dw.dws_call_result ===
    op.execute("""
    CREATE TABLE dw.dws_call_result (
        stat_date DATE NOT NULL,
        agent_id UUID,
        case_stage VARCHAR(10),
        total_calls INT NOT NULL DEFAULT 0,
        connected_count INT NOT NULL DEFAULT 0,
        no_answer_count INT NOT NULL DEFAULT 0,
        failed_count INT NOT NULL DEFAULT 0,
        ptp_count INT NOT NULL DEFAULT 0,
        refused_count INT NOT NULL DEFAULT 0,
        escalated_count INT NOT NULL DEFAULT 0,
        connected_rate FLOAT GENERATED ALWAYS AS (CASE WHEN total_calls=0 THEN 0 ELSE connected_count::FLOAT/total_calls END) STORED,
        ptp_rate FLOAT GENERATED ALWAYS AS (CASE WHEN connected_count=0 THEN 0 ELSE ptp_count::FLOAT/connected_count END) STORED,
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (stat_date, agent_id, case_stage)
    );
    """)
    op.execute("CREATE INDEX idx_dws_call_result_stat_date ON dw.dws_call_result(stat_date);")

    # === dw.dws_sentiment_trend ===
    op.execute("""
    CREATE TABLE dw.dws_sentiment_trend (
        session_id VARCHAR(100) NOT NULL,
        turn_number INT NOT NULL,
        sentiment_score FLOAT NOT NULL CHECK (sentiment_score >= 0 AND sentiment_score <= 1),
        trend_label VARCHAR(20) CHECK (trend_label IN ('rising','falling','stable','fluctuating')),
        delta_from_prev FLOAT,
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (session_id, turn_number)
    );
    """)

    # === dw.dws_strategy_effectiveness ===
    op.execute("""
    CREATE TABLE dw.dws_strategy_effectiveness (
        stat_date DATE NOT NULL,
        strategy_id UUID NOT NULL,
        strategy_name VARCHAR(100) NOT NULL,
        strategy_type VARCHAR(30) NOT NULL,
        total_calls INT NOT NULL DEFAULT 0,
        ptp_count INT NOT NULL DEFAULT 0,
        conversion_rate FLOAT GENERATED ALWAYS AS (CASE WHEN total_calls=0 THEN 0 ELSE ptp_count::FLOAT/total_calls END) STORED,
        avg_duration_seconds INT NOT NULL DEFAULT 0,
        avg_compliance_score INT NOT NULL DEFAULT 0,
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (stat_date, strategy_id)
    );
    """)

    # === dw.dws_agent_daily_summary ===
    op.execute("""
    CREATE TABLE dw.dws_agent_daily_summary (
        stat_date DATE NOT NULL,
        agent_id UUID NOT NULL,
        total_calls INT NOT NULL DEFAULT 0,
        total_talk_seconds INT NOT NULL DEFAULT 0,
        ptp_count INT NOT NULL DEFAULT 0,
        complaint_count INT NOT NULL DEFAULT 0,
        avg_compliance_score INT NOT NULL DEFAULT 0,
        qc_violation_count INT NOT NULL DEFAULT 0,
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
        PRIMARY KEY (stat_date, agent_id)
    );
    """)
    op.execute("CREATE INDEX idx_dws_agent_daily_summary_stat_date ON dw.dws_agent_daily_summary(stat_date);")

    # === dw.ads_daily_report ===
    op.execute("""
    CREATE TABLE dw.ads_daily_report (
        stat_date DATE PRIMARY KEY,
        total_calls INT NOT NULL DEFAULT 0,
        connected_rate FLOAT NOT NULL DEFAULT 0 CHECK (connected_rate >= 0 AND connected_rate <= 1),
        ptp_rate FLOAT NOT NULL DEFAULT 0 CHECK (ptp_rate >= 0 AND ptp_rate <= 1),
        avg_duration FLOAT NOT NULL DEFAULT 0,
        complaint_count INT NOT NULL DEFAULT 0,
        compliance_pass_rate FLOAT NOT NULL DEFAULT 0 CHECK (compliance_pass_rate >= 0 AND compliance_pass_rate <= 1),
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)

    # === dw.ads_user_features ===
    op.execute("""
    CREATE TABLE dw.ads_user_features (
        case_id UUID PRIMARY KEY,
        willingness_score INT CHECK (willingness_score BETWEEN 0 AND 100),
        ability_score INT CHECK (ability_score BETWEEN 0 AND 100),
        complaint_risk_score INT CHECK (complaint_risk_score BETWEEN 0 AND 100),
        overall_risk_level VARCHAR(10) CHECK (overall_risk_level IN ('low','medium','high')),
        historical_call_count INT DEFAULT 0,
        historical_ptp_count INT DEFAULT 0,
        historical_complaint_count INT DEFAULT 0,
        features JSONB DEFAULT '{}'::jsonb,
        etl_processed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
    );
    """)
    op.execute("CREATE INDEX idx_ads_user_features_overall_risk_level ON dw.ads_user_features(overall_risk_level);")

    # === 复合索引与部分索引 ===
    op.execute("CREATE INDEX idx_call_records_case_id_start_time ON call_records(case_id, start_time DESC);")
    op.execute("CREATE INDEX idx_qc_records_qc_result_create_time ON qc_records(qc_result, create_time DESC);")
    op.execute("CREATE INDEX idx_dialog_turns_session_id_turn_number ON dialog_turns(session_id, turn_number);")
    op.execute("CREATE INDEX idx_audit_logs_user_id_create_time ON audit_logs(user_id, create_time DESC);")
    op.execute("CREATE INDEX idx_llm_call_logs_trace_id_create_time ON llm_call_logs(trace_id, create_time DESC);")
    op.execute("CREATE INDEX idx_dws_call_result_stat_date_agent_id ON dw.dws_call_result(stat_date, agent_id);")
    op.execute("CREATE INDEX idx_cases_active ON cases(case_status) WHERE case_status IN ('pending','processing') AND is_delete = '0';")
    op.execute("CREATE INDEX idx_qc_records_pending_review ON qc_records(create_time DESC) WHERE qc_result = 'pending_review' AND is_delete = '0';")
    op.execute("CREATE INDEX idx_sensitive_words_active ON sensitive_words(category) WHERE status = 'active' AND is_delete = '0';")
    op.execute("CREATE INDEX idx_strategy_versions_active ON strategy_versions(strategy_type) WHERE status = 'active' AND is_delete = '0';")
    op.execute("CREATE INDEX idx_call_records_ai_calls ON call_records(start_time DESC) WHERE agent_id IS NULL AND is_delete = '0';")

    # === 表注释 ===
    op.execute("COMMENT ON TABLE cases IS '逾期案件主表';")
    op.execute("COMMENT ON COLUMN cases.phone_encrypted IS 'AES-256-GCM 加密存储，展示时脱敏';")
    op.execute("COMMENT ON TABLE users IS '系统用户与坐席主表，承载 RBAC 角色与坐席技能画像';")
    op.execute("COMMENT ON COLUMN users.password_hash IS 'bcrypt 哈希，cost factor=12，禁止明文存储';")
    op.execute("COMMENT ON TABLE call_records IS '全量通话记录，AI 和人工统一入库';")
    op.execute("COMMENT ON TABLE dialog_turns IS '每通电话逐轮对话，append-only，用于审计和模型训练';")
    op.execute("COMMENT ON COLUMN dialog_turns.reasoning_encrypted IS 'AI 决策推理过程，AES-256 加密存储，仅 role=admin 可解密';")
    op.execute("COMMENT ON TABLE qc_records IS '质检结果表，合规分=100-Σ扣分，下限 0';")
    op.execute("COMMENT ON TABLE llm_call_logs IS 'LLM 调用全量审计表，强制落库；confidence<0.7 禁止直接回复';")
    op.execute("COMMENT ON COLUMN llm_call_logs.prompt IS '入参，落库前经 mask.py 脱敏';")
    op.execute("COMMENT ON TABLE strategy_versions IS '策略版本表，统一管理各类策略版本与审批，支持回滚';")
    op.execute("COMMENT ON TABLE features_user IS '用户特征宽表，data-platform 离线计算产出';")
    op.execute("COMMENT ON TABLE etl_task_logs IS 'ETL 任务日志，append-only';")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS dw.ads_user_features;")
    op.execute("DROP TABLE IF EXISTS dw.ads_daily_report;")
    op.execute("DROP TABLE IF EXISTS dw.dws_agent_daily_summary;")
    op.execute("DROP TABLE IF EXISTS dw.dws_strategy_effectiveness;")
    op.execute("DROP TABLE IF EXISTS dw.dws_sentiment_trend;")
    op.execute("DROP TABLE IF EXISTS dw.dws_call_result;")
    op.execute("DROP TABLE IF EXISTS dw.dwd_call_records;")
    op.execute("DROP TABLE IF EXISTS dw.ods_call_records;")
    op.execute("DROP TABLE IF EXISTS etl_task_logs;")
    op.execute("DROP TABLE IF EXISTS audit_logs;")
    op.execute("DROP TABLE IF EXISTS llm_call_logs;")
    op.execute("DROP TABLE IF EXISTS review_cases;")
    op.execute("DROP TABLE IF EXISTS complaints;")
    op.execute("DROP TABLE IF EXISTS features_user;")
    op.execute("DROP TABLE IF EXISTS strategy_versions;")
    op.execute("DROP TABLE IF EXISTS call_strategies;")
    op.execute("DROP TABLE IF EXISTS assignment_strategies;")
    op.execute("DROP TABLE IF EXISTS qc_rules;")
    op.execute("DROP TABLE IF EXISTS sensitive_words;")
    op.execute("DROP TABLE IF EXISTS kb_chunks;")
    op.execute("DROP TABLE IF EXISTS kb_documents;")
    op.execute("DROP TABLE IF EXISTS intent_tags;")
    op.execute("DROP TABLE IF EXISTS qc_records;")
    op.execute("DROP TABLE IF EXISTS dialog_turns;")
    op.execute("DROP TABLE IF EXISTS call_records;")
    op.execute("DROP TABLE IF EXISTS users;")
    op.execute("DROP TABLE IF EXISTS cases;")

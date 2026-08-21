"""common.db 模块单元测试。"""
from sqlalchemy import inspect

from common.db.base import Base, PublicFieldsMixin
from common.db.models import AuditLog, LLMCallLog, StrategyVersion, User


class TestBase:
    def test_declarative_base(self):
        assert Base is not None
        assert hasattr(Base, "metadata")

    def test_public_fields_mixin_attributes(self):
        assert hasattr(PublicFieldsMixin, "id")
        assert hasattr(PublicFieldsMixin, "create_time")
        assert hasattr(PublicFieldsMixin, "update_time")
        assert hasattr(PublicFieldsMixin, "is_delete")


class TestUserModel:
    def test_table_name(self):
        assert User.__tablename__ == "users"

    def test_columns(self):
        mapper = inspect(User)
        col_names = {c.key for c in mapper.columns}
        assert "username" in col_names
        assert "password_hash" in col_names
        assert "real_name" in col_names
        assert "role" in col_names
        assert "agent_skills" in col_names
        assert "status" in col_names
        assert "last_login_at" in col_names
        assert "id" in col_names
        assert "create_time" in col_names
        assert "update_time" in col_names
        assert "is_delete" in col_names

    def test_username_unique(self):
        mapper = inspect(User)
        col = mapper.columns["username"]
        assert col.unique is True

    def test_role_nullable(self):
        mapper = inspect(User)
        col = mapper.columns["role"]
        assert col.nullable is False


class TestAuditLogModel:
    def test_table_name(self):
        assert AuditLog.__tablename__ == "audit_logs"

    def test_append_only_fields(self):
        mapper = inspect(AuditLog)
        col_names = {c.key for c in mapper.columns}
        assert "id" in col_names
        assert "create_time" in col_names
        assert "trace_id" in col_names
        assert "user_id" in col_names
        assert "action" in col_names
        assert "detail" in col_names
        assert "ip_address" in col_names

    def test_no_update_time_append_only(self):
        mapper = inspect(AuditLog)
        col_names = {c.key for c in mapper.columns}
        assert "update_time" not in col_names
        assert "is_delete" not in col_names


class TestLLMCallLogModel:
    def test_table_name(self):
        assert LLMCallLog.__tablename__ == "llm_call_logs"

    def test_columns(self):
        mapper = inspect(LLMCallLog)
        col_names = {c.key for c in mapper.columns}
        assert "trace_id" in col_names
        assert "session_id" in col_names
        assert "provider" in col_names
        assert "model" in col_names
        assert "prompt" in col_names
        assert "response" in col_names
        assert "token_usage" in col_names
        assert "latency_ms" in col_names
        assert "confidence" in col_names
        assert "temperature" in col_names
        assert "status" in col_names
        assert "error_msg" in col_names

    def test_append_only_fields(self):
        mapper = inspect(LLMCallLog)
        col_names = {c.key for c in mapper.columns}
        assert "update_time" not in col_names
        assert "is_delete" not in col_names


class TestStrategyVersionModel:
    def test_table_name(self):
        assert StrategyVersion.__tablename__ == "strategy_versions"

    def test_columns(self):
        mapper = inspect(StrategyVersion)
        col_names = {c.key for c in mapper.columns}
        assert "strategy_type" in col_names
        assert "params" in col_names
        assert "version" in col_names
        assert "status" in col_names
        assert "approved_by" in col_names

    def test_has_public_fields(self):
        mapper = inspect(StrategyVersion)
        col_names = {c.key for c in mapper.columns}
        assert "id" in col_names
        assert "create_time" in col_names
        assert "update_time" in col_names
        assert "is_delete" in col_names


class TestModelsRegistration:
    def test_all_models_registered_in_metadata(self):
        table_names = set(Base.metadata.tables.keys())
        assert "users" in table_names
        assert "audit_logs" in table_names
        assert "llm_call_logs" in table_names
        assert "strategy_versions" in table_names

"""common.config 模块单元测试。"""
import io
import json

import structlog

from common.config.logging import _mask_processor, setup_logging
from common.config.settings import Settings


class TestSettings:
    def test_default_values(self):
        s = Settings()
        assert s.ENV in ("dev", "test")
        assert s.PG_HOST == "localhost"
        assert s.PG_PORT == 5432
        assert s.PG_DB == "ai_collection"

    def test_pg_dsn_format(self):
        s = Settings()
        dsn = s.pg_dsn
        assert dsn.startswith("postgresql+asyncpg://")
        assert "ai_collection" in dsn

    def test_pg_dsn_sync_format(self):
        s = Settings()
        dsn = s.pg_dsn_sync
        assert dsn.startswith("postgresql+psycopg2://")

    def test_es_url(self):
        s = Settings()
        assert s.es_url == "http://localhost:9200"

    def test_platform_settings_excludes_business_params(self):
        """T33：common 只保留平台级参数，外呼/会话等业务参数下沉服务级配置。"""
        s = Settings()
        for field in (
            "CALL_MAX_CONCURRENCY",
            "CALL_TIMEZONE",
            "CALL_ALLOWED_HOURS",
            "CALL_DAILY_LIMIT_PER_USER",
            "CALL_RETRY_INTERVAL_MIN",
            "CALL_MAX_RETRY",
            "MAX_DIALOG_TURNS",
            "LLM_CONFIDENCE_THRESHOLD",
        ):
            assert field not in Settings.model_fields
        # LLM 熔断阈值属于平台级可靠性参数，仍在 common
        assert s.LLM_CIRCUIT_BREAKER_THRESHOLD == 3
        # RAG_SCORE_THRESHOLD 已迁至 rag-service RagSettings
        assert "QC_RESPONSE_TIMEOUT" not in Settings.model_fields


class TestLogging:
    def test_mask_processor_masks_phone(self):
        event_dict = {"message": "联系客户 13812341234 请尽快还款"}
        result = _mask_processor(None, None, event_dict)
        assert "13812341234" not in result["message"]
        assert "138****1234" in result["message"]

    def test_mask_processor_preserves_non_phone(self):
        event_dict = {"message": "正常日志消息无手机号"}
        result = _mask_processor(None, None, event_dict)
        assert result["message"] == "正常日志消息无手机号"

    def test_mask_processor_multiple_phones(self):
        event_dict = {"message": "13812341234 和 15912341234"}
        result = _mask_processor(None, None, event_dict)
        assert "13812341234" not in result["message"]
        assert "15912341234" not in result["message"]

    def test_mask_processor_non_string_values(self):
        event_dict = {"count": 42, "data": None, "message": "ok"}
        result = _mask_processor(None, None, event_dict)
        assert result["count"] == 42
        assert result["data"] is None

    def test_setup_logging_no_error(self):
        setup_logging("dev")
        setup_logging("prod")

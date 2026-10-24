"""QC-1.2：DD-02 §12 QC_* 默认值。"""
from app.core.config import QcSettings, qc_settings
from common.config.settings import Settings as PlatformSettings


_REQUIRED_QC_KEYS = (
    "QC_RAG_SENSITIVE_BASE",
    "QC_RAG_INITIAL_TIMEOUT",
    "QC_RAG_HOURLY_CRON",
    "QC_DP_BASE",
    "QC_DP_SESSION_PATH",
    "QC_DP_PENDING_QC_PATH",
    "QC_DP_TIMEOUT",
    "QC_LLM_MODEL",
    "QC_LLM_TIMEOUT",
    "QC_LLM_FALLBACK_CHAIN",
    "QC_MAX_INFLIGHT",
    "QC_INFLIGHT_WAIT_TIMEOUT",
    "QC_LLM_RETRY",
    "QC_LLM_DEGRADE_AFTER",
    "QC_DEDUP_WINDOW_MS",
    "QC_AUTO_START_ON_ENDED",
    "QC_AUTO_COMPLEMENT_AFTER_ETL",
    "QC_TEXT_READY_RETRY_SEC",
    "QC_TEXT_READY_RETRY_MAX",
    "QC_ALERT_LEVEL_MIN",
    "QC_BATCH_MAX",
    "QC_MOCK_LLM",
    "QC_DEFAULT_ACTIVE_RULES_VERSION",
    "QC_R14_RAG_ENABLED",
)


class TestQcSettings:
    def test_all_section12_keys_present(self):
        fields = QcSettings.model_fields
        missing = [k for k in _REQUIRED_QC_KEYS if k not in fields]
        assert missing == []

    def test_r14_rag_disabled(self):
        assert qc_settings.QC_R14_RAG_ENABLED is False

    def test_text_ready_retry_defaults(self):
        assert qc_settings.QC_TEXT_READY_RETRY_SEC == 30
        assert qc_settings.QC_TEXT_READY_RETRY_MAX == 20

    def test_default_active_rules_version(self):
        assert qc_settings.QC_DEFAULT_ACTIVE_RULES_VERSION == "v1.0"

    def test_hosts_omit_api_v1(self):
        assert "/api/v1" not in qc_settings.QC_RAG_SENSITIVE_BASE
        assert "/api/v1" not in qc_settings.QC_DP_BASE
        assert qc_settings.QC_RAG_SENSITIVE_BASE == "http://rag-service:8002"
        assert qc_settings.QC_DP_BASE == "http://data-platform:8001"

    def test_dp_paths_are_paths_not_hosts(self):
        assert qc_settings.QC_DP_SESSION_PATH.startswith("/api/v1/")
        assert qc_settings.QC_DP_PENDING_QC_PATH.startswith("/api/v1/")

    def test_forbidden_keys_absent(self):
        fields = QcSettings.model_fields
        assert "QC_RESPONSE_TIMEOUT" not in fields
        assert "QC_EVENT_ESCALATE_ENABLED" not in fields
        assert "QC_RESPONSE_TIMEOUT" not in PlatformSettings.model_fields

    def test_redis_db_default_is_3(self):
        assert qc_settings.QC_REDIS_DB == 3

    def test_remaining_section12_defaults(self):
        assert qc_settings.QC_RAG_INITIAL_TIMEOUT == 3
        assert qc_settings.QC_RAG_HOURLY_CRON == "0 * * * *"
        assert qc_settings.QC_DP_TIMEOUT == 5
        assert qc_settings.QC_LLM_MODEL == "deepseek-flash"
        assert qc_settings.QC_LLM_TIMEOUT == 20
        assert qc_settings.QC_LLM_FALLBACK_CHAIN == ["qwen-turbo"]
        assert qc_settings.QC_MAX_INFLIGHT == 3
        assert qc_settings.QC_INFLIGHT_WAIT_TIMEOUT == 0
        assert qc_settings.QC_LLM_RETRY == 3
        assert qc_settings.QC_LLM_DEGRADE_AFTER == 3
        assert qc_settings.QC_DEDUP_WINDOW_MS == 30000
        assert qc_settings.QC_AUTO_START_ON_ENDED is True
        assert qc_settings.QC_AUTO_COMPLEMENT_AFTER_ETL is True
        assert qc_settings.QC_ALERT_LEVEL_MIN == "high"
        assert qc_settings.QC_BATCH_MAX == 100
        assert qc_settings.QC_MOCK_LLM is False

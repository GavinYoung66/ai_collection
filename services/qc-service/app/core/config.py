"""qc-service 产品开关：DD-02 §12 全部 QC_*。平台 PG/Redis host 仍读 common Settings。"""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_HOST_FIELDS = ("QC_RAG_SENSITIVE_BASE", "QC_DP_BASE")


class QcSettings(BaseSettings):
    """质检产品配置。不继承平台 Settings；平台 Settings 已无 QC_RESPONSE_TIMEOUT。"""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    QC_RAG_SENSITIVE_BASE: str = "http://rag-service:8002"
    QC_RAG_INITIAL_TIMEOUT: int = 3
    QC_RAG_HOURLY_CRON: str = "0 * * * *"
    QC_DP_BASE: str = "http://data-platform:8001"
    QC_DP_SESSION_PATH: str = "/api/v1/data/sessions/{session_id}/qc-source"
    QC_DP_PENDING_QC_PATH: str = "/api/v1/data/sessions:pending-qc"
    QC_DP_TIMEOUT: int = 5
    QC_LLM_MODEL: str = "qwen-plus"
    QC_LLM_TIMEOUT: int = 5
    QC_LLM_RETRY: int = 3
    QC_LLM_DEGRADE_AFTER: int = 3
    QC_DEDUP_WINDOW_MS: int = 30000
    QC_AUTO_START_ON_ENDED: bool = True
    QC_AUTO_COMPLEMENT_AFTER_ETL: bool = True
    QC_TEXT_READY_RETRY_SEC: int = 30
    QC_TEXT_READY_RETRY_MAX: int = 20
    QC_ALERT_LEVEL_MIN: str = "high"
    QC_BATCH_MAX: int = 100
    QC_MOCK_LLM: bool = False
    QC_DEFAULT_ACTIVE_RULES_VERSION: str = "v1.0"
    QC_R14_RAG_ENABLED: bool = False
    QC_REDIS_DB: int = 3

    @model_validator(mode="after")
    def _hosts_must_not_contain_api_prefix(self) -> QcSettings:
        for name in _HOST_FIELDS:
            value = getattr(self, name)
            if "/api/v1" in str(value):
                raise ValueError(f"{name} must not contain /api/v1")
        return self


qc_settings = QcSettings()

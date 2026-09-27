"""data-platform 产品配置。

平台 PG/Redis host 仍读 common Settings（唯一事实源）。
QC_REDIS_DB 与 qc-service 同名同库（同一 Redis 实例 SELECT 3），
data-platform 不另起 Redis 实例，且禁止使用 0 号库。
"""
from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class DataPlatformSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 最小闭环事件通道（etl.task.done / qc.check.completed）与 qc-service 共用逻辑库。
    QC_REDIS_DB: int = 3

    # 投诉服务内部 HTTP 地址（本机闭环调用，不经过 api-gateway）。
    COMPLAINT_SERVICE_URL: str = "http://localhost:8006/api/v1"
    COMPLAINT_HTTP_TIMEOUT: float = 5.0
    COMPLAINT_HTTP_RETRY: int = 3

    # 质检服务内部 HTTP（信封瘦身后反拉 records 详情）。
    QC_SERVICE_URL: str = "http://localhost:8003/api/v1"
    QC_HTTP_TIMEOUT: float = 5.0
    QC_HTTP_RETRY: int = 3

    # 种子入库后等待 qc.check.completed 的超时告警阈值（分钟）。
    QC_TIMEOUT_MINUTES: int = 10

    # judge 跑图有界预算：投诉创建全量，judge/review 仅前 N 单（LLM 瓶颈+成本约束）。
    # 超出预算的投诉停在"已创建未研判"。可用环境变量 JUDGE_BUDGET 覆盖。
    JUDGE_BUDGET: int = 20

    @model_validator(mode="after")
    def _redis_db_must_not_be_default_zero(self) -> DataPlatformSettings:
        if self.QC_REDIS_DB == 0:
            raise ValueError("QC_REDIS_DB 禁止使用 0 号库；最小闭环事件必须走 QC_REDIS_DB（默认 3）")
        return self


dp_settings = DataPlatformSettings()

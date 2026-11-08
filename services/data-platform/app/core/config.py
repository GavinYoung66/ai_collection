"""data-platform 产品配置。

平台级参数（PG/Redis/密钥/公共超时）统一继承 common Settings（唯一事实源）。
QC_REDIS_DB 与 qc-service 同名同库（同一 Redis 实例 SELECT 3），
data-platform 不另起 Redis 实例，且禁止使用 0 号库。
"""
from __future__ import annotations

from pydantic import model_validator

from common.config.settings import Settings


class DataPlatformSettings(Settings):
    """数据平台业务配置；平台参数继承自 common Settings。"""

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

    # 事件早于落库时的补偿：有限重试次数 + 固定间隔；耗尽后告警，不静默丢弃。
    QC_RECOVERY_MAX_ATTEMPTS: int = 10
    QC_RECOVERY_INTERVAL_SECONDS: float = 3.0

    # T91：数据面服务间凭证——静态预共享 token（与调用方 QC_DP_SERVICE_TOKEN 同值配置）。
    # 配置后先恒定时间比对该值（命中即放行，支持非 JWT 静态形态）；
    # 未配置或不命中则走内部 JWT 验签且 role 必须为 "service"。缺省空 = 仅 JWT 模式。
    DP_SERVICE_TOKEN: str = ""

    @model_validator(mode="after")
    def _redis_db_must_not_be_default_zero(self) -> DataPlatformSettings:
        if self.QC_REDIS_DB == 0:
            raise ValueError("QC_REDIS_DB 禁止使用 0 号库；最小闭环事件必须走 QC_REDIS_DB（默认 3）")
        return self


dp_settings = DataPlatformSettings()

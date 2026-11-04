"""call-service 业务配置：外呼合规、并发与会话轮次。

平台级参数（库连接/密钥/公共超时）统一继承 ``common.config.settings.Settings``；
本模块只承载外呼领域业务参数，环境变量名保持不变（``CALL_*`` / ``MAX_DIALOG_TURNS`` /
``LLM_CONFIDENCE_THRESHOLD``），供 ``common.utils.time_guard`` / ``CallSemaphore`` /
``CCCProvider`` 由调用方显式注入。
"""
from common.config.settings import Settings


class CallSettings(Settings):
    # 外呼合规时区：时段判定一律按此 IANA 时区（默认北京时间），与容器 TZ 无关
    CALL_TIMEZONE: str = "Asia/Shanghai"
    CALL_ALLOWED_HOURS: str = "08:00-21:00"
    CALL_DAILY_LIMIT_PER_USER: int = 3
    CALL_RETRY_INTERVAL_MIN: int = 30
    CALL_MAX_RETRY: int = 3
    CALL_MAX_CONCURRENCY: int = 50
    MAX_DIALOG_TURNS: int = 15
    LLM_CONFIDENCE_THRESHOLD: float = 0.7


settings = CallSettings()

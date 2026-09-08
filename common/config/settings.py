"""全局配置单例，pydantic-settings + .env 注入。

仅放跨服务平台项（库连接、共享密钥占位、公共超时等）。
RAG 产品开关与检索/入库参数见 `services/rag-service/app/rag_settings.py`。
"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 环境
    ENV: str = "dev"

    # PostgreSQL
    PG_HOST: str = "localhost"
    PG_PORT: int = 5432
    PG_USER: str = "ai_collection"
    PG_PASSWORD: str = "change_me"
    PG_DB: str = "ai_collection"

    # Redis
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # Milvus / Elasticsearch / Embedding（基础设施；rag 与其它服务可共用）
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530
    ES_HOST: str = "localhost"
    ES_PORT: int = 9200
    EMBEDDING_SERVICE_URL: str = "http://localhost:8100"
    RERANK_SERVICE_URL: str = "http://localhost:8101"

    # LLM（跨服务 Key / 默认模型；业务开关不放这里）
    LLM_API_KEY: str = "change_me"
    LLM_MODEL: str = "qwen-plus"
    LLM_TIMEOUT: int = 10
    LLM_CONFIDENCE_THRESHOLD: float = 0.7
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DASHSCOPE_API_KEY: str = ""

    # ASR / TTS / CCC（呼叫相关公共密钥占位）
    ASR_APP_KEY: str = "change_me"
    ASR_ACCESS_TOKEN: str = "change_me"
    TTS_APP_KEY: str = "change_me"
    TTS_ACCESS_TOKEN: str = "change_me"
    CCC_ACCESS_KEY_ID: str = "change_me"
    CCC_ACCESS_KEY_SECRET: str = "change_me"

    # AES / JWT
    AES_KEY: str = "change_me"
    JWT_SECRET: str = "change_me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # 业务参数（外呼 / 质检 / 熔断）
    CALL_ALLOWED_HOURS: str = "08:00-21:00"
    CALL_DAILY_LIMIT_PER_USER: int = 3
    CALL_RETRY_INTERVAL_MIN: int = 30
    CALL_MAX_RETRY: int = 3
    CALL_MAX_CONCURRENCY: int = 50
    MAX_DIALOG_TURNS: int = 15
    QC_RESPONSE_TIMEOUT: int = 3
    LLM_CIRCUIT_BREAKER_THRESHOLD: int = 3
    LLM_CIRCUIT_BREAKER_RECOVERY: int = 60

    @property
    def pg_dsn(self) -> str:
        return f"postgresql+asyncpg://{self.PG_USER}:{self.PG_PASSWORD}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"

    @property
    def pg_dsn_sync(self) -> str:
        return f"postgresql+psycopg2://{self.PG_USER}:{self.PG_PASSWORD}@{self.PG_HOST}:{self.PG_PORT}/{self.PG_DB}"

    @property
    def es_url(self) -> str:
        return f"http://{self.ES_HOST}:{self.ES_PORT}"

    @property
    def deepseek_api_key_resolved(self) -> str:
        """DeepSeek Key：优先 DEEPSEEK_API_KEY，兼容 pydantic 未映射的 DeepSeek_API_KEY env。"""
        if self.DEEPSEEK_API_KEY:
            return self.DEEPSEEK_API_KEY
        import os

        return os.environ.get("DeepSeek_API_KEY", "")


settings = Settings()

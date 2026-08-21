"""全局配置单例，pydantic-settings + .env 注入。"""
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

    # Milvus
    MILVUS_HOST: str = "localhost"
    MILVUS_PORT: int = 19530

    # Elasticsearch
    ES_HOST: str = "localhost"
    ES_PORT: int = 9200

    # Embedding / Rerank
    EMBEDDING_SERVICE_URL: str = "http://localhost:8100"
    RERANK_SERVICE_URL: str = "http://localhost:8101"

    # LLM
    LLM_API_KEY: str = "change_me"
    LLM_MODEL: str = "qwen-plus"
    LLM_TIMEOUT: int = 5
    LLM_CONFIDENCE_THRESHOLD: float = 0.7

    # ASR
    ASR_APP_KEY: str = "change_me"
    ASR_ACCESS_TOKEN: str = "change_me"

    # TTS
    TTS_APP_KEY: str = "change_me"
    TTS_ACCESS_TOKEN: str = "change_me"

    # CCC
    CCC_ACCESS_KEY_ID: str = "change_me"
    CCC_ACCESS_KEY_SECRET: str = "change_me"

    # AES
    AES_KEY: str = "change_me"

    # JWT
    JWT_SECRET: str = "change_me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_HOURS: int = 24

    # RAG Service
    KB_CHUNK_SIZE: int = 500
    KB_CHUNK_OVERLAP: int = 50
    KB_CHUNK_MIN_LENGTH: int = 10
    KB_RETRIEVAL_TOP_N: int = 20
    KB_RRF_K: int = 60
    KB_RERANK_THRESHOLD: float = 0.35
    KB_CACHE_TTL: int = 3600
    KB_CACHE_ENABLED: bool = True
    RAG_REDIS_DB: int = 2
    # 超时与请求级时限（对齐 step6/01 §4：通用外部调用 5s/3 次重试）
    EMBED_TIMEOUT: float = 5.0  # 单条向量化超时（秒），规范值
    EMBED_BATCH_TIMEOUT: float = 30.0  # 批量向量化超时上限（秒），CPU 部署可调大
    RERANK_TIMEOUT: float = 10.0  # rerank 超时（秒）：实测冷启动约 5s 留余量，GPU 部署可收紧至 5s
    KB_SEARCH_TIMEOUT: float = 15.0  # 检索请求级总时限（秒），防慢调用堆积

    # 业务参数
    CALL_ALLOWED_HOURS: str = "08:00-21:00"
    CALL_DAILY_LIMIT_PER_USER: int = 3
    CALL_RETRY_INTERVAL_MIN: int = 30
    CALL_MAX_RETRY: int = 3
    CALL_MAX_CONCURRENCY: int = 50
    MAX_DIALOG_TURNS: int = 15
    RAG_SCORE_THRESHOLD: float = 0.6
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


settings = Settings()

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

    # === RAG V2 功能开关（默认全 false = V1 行为） ===
    KB_QUERY_REWRITE_ENABLED: bool = False       # Query 改写（LLM 纠错/术语归一/扩展）
    KB_ROUTING_ENABLED: bool = False             # 动态路由（意图规则分类→权重调整）
    KB_WEIGHTING_ENABLED: bool = False           # 业务加权（新通话 ×1.5、高危 ×1.3）
    KB_ROW_LEVEL_ENABLED: bool = False           # 行级权限（角色/部门过滤 KB 分类）
    KB_MULTITURN_ENABLED: bool = False           # 多轮对话（Redis session 上下文）
    KB_EMBED_DEGRADED_KEEP_TEXT: bool = False    # Embedding 失败降级入库（仅 ES，无向量）
    ES_DUAL_WRITE: bool = False                  # ES 双写过渡（kb_bm25 + kb_chunks_bm25）

    # === RAG V3 功能开关（默认全 false = V2 行为） ===
    KB_OCR_ASR_DEGRADED_KEEP_TEXT: bool = False  # OCR/ASR 解析失败降级入库（跳过解析/占位入库）
    KB_ES_DEGRADED_KEEP_MILVUS: bool = False     # ES 索引失败降级（保留 Milvus + 异步补索引）

    # === RAG V4 功能开关（默认全 false = V3 行为） ===
    KB_SUMMARY_ENABLED: bool = False       # 摘要元数据检索增强（ES doc_summary 加权 + 响应 summary 填充）
    KB_SUMMARY_ES_BOOST: float = 1.0       # 摘要字段 BM25 加权分（默认 1.0 = 与正文同权）
    KB_SUMMARY_MAX_LEN: int = 200          # 摘要长度上限（字符），超限返回 B0001
    KB_ASR_OPENING_TRIM_ENABLED: bool = False  # ASR 通话开场白规则裁剪（转写文本开头，规则见 conf/asr_opening_rules.yaml）

    # === RAG V2 OCR/ASR/LLM 配置 ===
    OCR_ENDPOINT: str = "http://ocr-service:8200/ocr"
    OCR_TIMEOUT: int = 30
    ASR_PROVIDER: str = "dashscope"  # dashscope | whisper
    ASR_TIMEOUT: int = 60
    LLM_DASHSCOPE_ENDPOINT: str = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    LLM_TIMEOUT: int = 10
    DASHSCOPE_API_KEY: str = ""  # 从 env 读，可选，未配置则 Rewrite/ASR 走 disabled

    # === RAG V2 Session 配置 ===
    SESSION_TTL_SECONDS: int = 1800       # 会话 TTL 30min
    SESSION_HISTORY_CAP: int = 10         # 每会话最多保存 10 轮
    SESSION_REWRITE_LOOKBACK: int = 3     # Query 改写时回看最近 3 轮

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

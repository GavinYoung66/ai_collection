"""RAG 产品配置：KB_* 与检索/入库开关归属 rag-service，不再堆在 common 平台 Settings。"""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class RagSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # RAG 核心检索 / 入库
    KB_CHUNK_SIZE: int = 500
    KB_CHUNK_OVERLAP: int = 50
    KB_CHUNK_MIN_LENGTH: int = 10
    KB_RETRIEVAL_TOP_N: int = 20
    KB_DOC_CHUNK_KEEP: int = 5
    KB_RRF_K: int = 60
    KB_RERANK_THRESHOLD: float = 0.35
    KB_CACHE_TTL: int = 3600
    KB_CACHE_ENABLED: bool = True
    KB_MAX_FILE_SIZE: int = 20 * 1024 * 1024
    KB_PARSE_TIMEOUT: float = 30.0
    KB_INGEST_JOB_TIMEOUT: float = 1800.0
    KB_INGEST_STAGING_DIR: str = "data/ingest_staging"
    KB_HEALTH_DEGRADED_MODE: bool = False
    RAG_REDIS_DB: int = 2
    EMBED_TIMEOUT: float = 5.0
    EMBED_BATCH_TIMEOUT: float = 30.0
    RERANK_TIMEOUT: float = 10.0
    KB_SEARCH_TIMEOUT: float = 15.0

    # V2 开关
    KB_QUERY_REWRITE_ENABLED: bool = False
    KB_ROUTING_ENABLED: bool = False
    KB_WEIGHTING_ENABLED: bool = False
    KB_ROW_LEVEL_ENABLED: bool = False
    KB_MULTITURN_ENABLED: bool = False
    KB_EMBED_DEGRADED_KEEP_TEXT: bool = False
    ES_DUAL_WRITE: bool = False

    # V3
    KB_OCR_ASR_DEGRADED_KEEP_TEXT: bool = False
    KB_ES_DEGRADED_KEEP_MILVUS: bool = False

    # V4
    KB_SUMMARY_ENABLED: bool = False
    KB_SUMMARY_ES_BOOST: float = 1.0
    KB_SUMMARY_MAX_LEN: int = 200
    KB_ASR_OPENING_TRIM_ENABLED: bool = False

    # V5 + LLM answer 编排（仍可读平台 LLM_* Key）
    KB_LLM_ANSWER_ENABLED: bool = False
    LLM_PROVIDER: str = "dashscope"
    LLM_MAX_CONCURRENCY: int = 4
    LLM_LOCAL_BASE_URL: str = ""
    LLM_LOCAL_API_KEY: str = ""
    KB_IMAGE_EXTRACT_ENABLED: bool = False
    KB_TABLE_EXTRACT_ENABLED: bool = False
    KB_EXCEL_SHEET_ALL: bool = True
    KB_EXCEL_HEADER_ROW: int = 1
    KB_ASR_AMR_WMA_ENABLED: bool = False
    KB_ASSETS_DIR: str = "data/assets"
    KB_ASSETS_MAX_SIZE: int = 1024

    LANGSMITH_ENABLED: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGSMITH_PROJECT: str = "rag-service"
    LANGSMITH_ENDPOINT: str = "https://api.smith.langchain.com"

    OCR_ENDPOINT: str = "http://ocr-service:8200/ocr"
    OCR_TIMEOUT: int = 30
    ASR_PROVIDER: str = "dashscope"
    ASR_TIMEOUT: int = 60
    LLM_DASHSCOPE_ENDPOINT: str = (
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    )

    SESSION_TTL_SECONDS: int = 1800
    SESSION_HISTORY_CAP: int = 10
    SESSION_REWRITE_LOOKBACK: int = 3

    RAG_SCORE_THRESHOLD: float = 0.6


rag_settings = RagSettings()

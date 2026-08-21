"""FastAPI 依赖注入 + 统一响应助手。"""
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import structlog
from fastapi import UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from common.config.settings import settings
from common.db.session import async_session_factory
from common.trace.middleware import get_trace_id
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from app.vectorstore.rerank_client import RerankClient

logger = structlog.get_logger()

_milvus_client: MilvusClient | None = None
_es_client: ESClient | None = None
_embed_client: EmbedClient | None = None
_rerank_client: RerankClient | None = None
_redis_client: Redis | None = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.RAG_REDIS_DB,
            decode_responses=True,
        )
    return _redis_client


def get_milvus_client() -> MilvusClient:
    global _milvus_client
    if _milvus_client is None:
        _milvus_client = MilvusClient()
    return _milvus_client


def get_es_client() -> ESClient:
    global _es_client
    if _es_client is None:
        _es_client = ESClient()
    return _es_client


async def get_embed_client() -> EmbedClient:
    global _embed_client
    if _embed_client is None:
        redis = await get_redis()
        _embed_client = EmbedClient(redis=redis)
    return _embed_client


async def get_rerank_client() -> RerankClient:
    global _rerank_client
    if _rerank_client is None:
        redis = await get_redis()
        _rerank_client = RerankClient(redis=redis)
    return _rerank_client


def success_response(data, message: str = "success") -> dict:
    return {
        "code": "0",
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
    }


async def save_upload_file(file: UploadFile) -> Path:
    suffix = Path(file.filename or "").suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    content = await file.read()
    tmp.write(content)
    tmp.close()
    return Path(tmp.name)

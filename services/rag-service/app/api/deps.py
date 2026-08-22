"""FastAPI 依赖注入 + 统一响应助手。"""
import tempfile
from collections.abc import AsyncGenerator
from pathlib import Path

import structlog
import ulid
from fastapi import UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from common.config.settings import settings
from common.db.session import async_session_factory
from common.trace.middleware import get_trace_id
from app.services.asr_client import ASRClient
from app.services.ocr_client import OCRClient
from app.services.query_rewrite_service import RewriteService
from app.services.routing_service import RoutingService
from app.services.row_access_service import RowAccessService
from app.services.session_service import SessionService
from app.services.weighting_service import WeightingService
from app.vectorstore.embed_client import EmbedClient
from app.vectorstore.es_client import ESClient
from app.vectorstore.milvus_client import MilvusClient
from app.vectorstore.rerank_client import RerankClient

logger = structlog.get_logger()

_milvus_client: MilvusClient | None = None
_es_client: ESClient | None = None
_embed_client: EmbedClient | None = None
_rerank_client: RerankClient | None = None
_ocr_client: OCRClient | None = None
_asr_client: ASRClient | None = None
_redis_client: Redis | None = None
# V2 服务单例
_rewrite_service: RewriteService | None = None
_routing_service: RoutingService | None = None
_weighting_service: WeightingService | None = None
_row_access_service: RowAccessService | None = None
_session_service: SessionService | None = None


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


def generate_session_id() -> str:
    """生成 ULID 字符串作为会话 ID（时间有序、26 字符 base32）。"""
    return str(ulid.new())


def get_session_id(request: Request) -> str:
    """读取请求头 X-Session-Id；缺失则生成新 ULID。

    - 显式截断至 64 字符上限，与 kb_search_logs.session_id VARCHAR(64) 对齐。
    """
    raw = request.headers.get("X-Session-Id")
    if raw:
        return raw.strip()[:64]
    return generate_session_id()


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


async def get_ocr_client() -> OCRClient:
    global _ocr_client
    if _ocr_client is None:
        redis = await get_redis()
        _ocr_client = OCRClient(redis=redis)
    return _ocr_client


async def get_asr_client() -> ASRClient:
    global _asr_client
    if _asr_client is None:
        redis = await get_redis()
        _asr_client = ASRClient(redis=redis)
    return _asr_client


# ===== V2 服务工厂 =====

async def get_rewrite_service() -> RewriteService:
    global _rewrite_service
    if _rewrite_service is None:
        redis = await get_redis()
        _rewrite_service = RewriteService(redis=redis)
    return _rewrite_service


def get_routing_service() -> RoutingService:
    global _routing_service
    if _routing_service is None:
        _routing_service = RoutingService()
    return _routing_service


def get_weighting_service() -> WeightingService:
    global _weighting_service
    if _weighting_service is None:
        _weighting_service = WeightingService()
    return _weighting_service


def get_row_access_service() -> RowAccessService:
    global _row_access_service
    if _row_access_service is None:
        _row_access_service = RowAccessService()
    return _row_access_service


async def get_session_service() -> SessionService:
    global _session_service
    if _session_service is None:
        redis = await get_redis()
        _session_service = SessionService(redis=redis)
    return _session_service


def get_jwt_payload(request: Request) -> dict | None:
    """从 Authorization 头提取 JWT payload；无 token 或校验失败返回 None（V1 兼容）。

    搜索接口权限 ALL，不因 JWT 缺失/无效而 401；
    RowAccessService 在 payload=None 时原样放行（V1 行为）。
    """
    header = request.headers.get("Authorization", "")
    if not header.startswith("Bearer "):
        return None
    token = header[len("Bearer "):].strip()
    if not token:
        return None
    try:
        from common.security.rbac import verify_token
        return verify_token(token)
    except Exception:
        return None


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

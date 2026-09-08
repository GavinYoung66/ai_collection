"""FastAPI 依赖注入 + 统一响应助手。"""
import os
import tempfile
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import structlog
import ulid
from fastapi import UploadFile
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.settings import settings
from common.db.session import async_session_factory
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.trace.middleware import get_trace_id
from app.services.asr_client import ASRClient
from app.services.llm_service import LLMService
from app.services.ocr_client import OCRClient
from app.services.query_rewrite_service import RewriteService
from app.services.routing_service import RoutingService
from app.services.row_access_service import RowAccessService
from app.services.sensitive_words_sync import SensitiveWordsSyncService
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
# V5 服务单例
_llm_service: LLMService | None = None


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


async def get_llm_service() -> LLMService:
    """V5：LLM 回答服务单例（开关门控 + 熔断 + 信号量 + 合规红线）。

    - word_provider：敏感词词库（红线关键词来源）——DB 优先 + 文件兜底
      （SensitiveWordsSyncService 既有逻辑），每次短 session 读取，
      LLMService 内部以 TTL 300s 缓存词集，不阻塞请求主链路；
      读取失败按空词集处理（LLM 回答仍可用，仅红线校验不生效并告警）。
    """
    global _llm_service
    if _llm_service is None:
        redis = await get_redis()

        async def _fetch_redline_words() -> set[str]:
            try:
                async with async_session_factory() as db:
                    text = await SensitiveWordsSyncService(
                        db, redis
                    ).get_current_dict()
                return {w.strip() for w in text.splitlines() if w.strip()}
            except Exception as e:
                logger.warning("llm_redline_words_fetch_failed", error=str(e))
                return set()

        _llm_service = LLMService(redis=redis, word_provider=_fetch_redline_words)
    return _llm_service


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


async def save_upload_to_staging(file: UploadFile, doc_id: uuid.UUID) -> Path:
    """保存上传文件到异步入库 staging 目录（后台任务结束后删除）。"""
    suffix = Path(file.filename or "").suffix
    max_size = settings.KB_MAX_FILE_SIZE
    filename = file.filename or ""
    declared_size = getattr(file, "size", None)
    logger.info(
        "upload_received",
        filename=filename,
        declared_size=declared_size,
        max_size=max_size,
        staging_doc_id=str(doc_id),
    )
    if isinstance(declared_size, int) and declared_size > max_size:
        raise BizException(
            ErrorCode.B0001,
            f"上传文件超过大小上限 {max_size // (1024 * 1024)}MB",
        )

    staging_dir = Path(settings.KB_INGEST_STAGING_DIR)
    staging_dir.mkdir(parents=True, exist_ok=True)
    dest = staging_dir / f"{doc_id}{suffix}"

    content = await file.read()
    if len(content) > max_size:
        raise BizException(
            ErrorCode.B0001,
            f"上传文件超过大小上限 {max_size // (1024 * 1024)}MB",
        )
    dest.write_bytes(content)
    return dest


async def save_upload_file(file: UploadFile) -> Path:
    """保存上传文件到临时路径（先大小校验、后落盘，出错不残留）。

    - 大小校验：优先使用 Content-Length（UploadFile.size），缺失则 read 后按字节校验；
      超 `KB_MAX_FILE_SIZE`（默认 20MB）抛 B0001，校验先于任何解析/落库；
    - 临时文件保留 delete=False（兼容 Windows 二次打开限制），
      读取/写入/校验失败时由本函数兜底 unlink，不向 /tmp 泄漏；
    - 正常返回后由调用方（upload 端点 finally）负责删除。
    """
    suffix = Path(file.filename or "").suffix
    max_size = settings.KB_MAX_FILE_SIZE
    filename = file.filename or ""
    declared_size = getattr(file, "size", None)
    # 请求进入：记录文件名与 Content-Length（无头时为 None）
    logger.info(
        "upload_received",
        filename=filename,
        declared_size=declared_size,
        max_size=max_size,
    )
    if isinstance(declared_size, int) and declared_size > max_size:
        logger.info(
            "upload_size_check_rejected",
            filename=filename,
            max_size=max_size,
            actual_size=declared_size,
            check_source="content_length",
        )
        raise BizException(
            ErrorCode.B0001,
            f"上传文件超过大小上限 {max_size // (1024 * 1024)}MB",
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        content = await file.read()
        if len(content) > max_size:
            logger.info(
                "upload_size_check_rejected",
                filename=filename,
                max_size=max_size,
                actual_size=len(content),
                check_source="read_bytes",
            )
            raise BizException(
                ErrorCode.B0001,
                f"上传文件超过大小上限 {max_size // (1024 * 1024)}MB",
            )
        logger.info(
            "upload_size_check_passed",
            filename=filename,
            max_size=max_size,
            actual_size=len(content),
        )
        tmp.write(content)
        tmp.close()
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise
    return Path(tmp.name)

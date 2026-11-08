"""FastAPI 依赖注入 + 统一响应助手。"""
import os
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
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
from common.security.rbac import verify_token
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

# T127（spec platform/runtime-correctness：惰性单例初始化线程安全）：
# 下方 13 个惰性单例 getter（5 个同步 def + 8 个 async def，含 get_redis）
# 原为无锁 check-then-act——并发首调（多线程/多 worker）会各自构造一个实例，
# 其中 get_llm_service 的实例分裂会让 LLMService 内部并发信号量 ×N 放大。
# 裁决：统一一把进程级 threading.Lock 做 DCL（双重检查锁定）——
# 1) 不用 asyncio.Lock：模块级 asyncio.Lock 在 Python 3.10+ 首次 acquire 时
#    绑定 event loop，pytest-asyncio 每用例新建 loop，跨用例复用即抛
#    RuntimeError；且 5 个同步 getter 无法在同步上下文使用 asyncio.Lock。
# 2) 各 getter 构造临界区均为纯同步属性赋值（Redis/httpx/LLMService 等
#    构造不建立网络连接；RoutingService/RowAccessService 仅读本地 YAML），
#    持锁微秒级；async getter 唯一的 await（get_redis）置于锁外，
#    临界区内零 await，不阻塞事件循环。
# 3) 兼容既有测试注入形态：monkeypatch.setattr("app.api.deps._xxx", stub)
#    直接赋值模块级变量——替身非 None 时首检命中直接返回替身，不进锁，
#    无需额外 reset 钩子。
_SINGLETON_LOCK = threading.Lock()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


async def get_redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        with _SINGLETON_LOCK:  # T127：DCL 双检，并发首调仅构造一次
            if _redis_client is None:
                _redis_client = Redis(
                    host=settings.REDIS_HOST,
                    port=settings.REDIS_PORT,
                    db=settings.RAG_REDIS_DB,
                    decode_responses=True,
                )
    return _redis_client


# T77：会话身份绑定（owner 归属）内存 store——session_id → (owner, bound_at)。
# owner 为内存 store 追加字段，无持久化 schema 变更（design 2.3.2）；
# 匿名会话 TTL 缩短（1h 过期自动失效），登录 owner 绑定 24h。
# T102（spec runtime-correctness：常驻进程内存有界）：OrderedDict 保序 + 容量
# 上限显式常量——写入前先清扫 TTL 过期条目，仍超限按插入序驱逐最旧；攻击者以
# 随机 X-Session-Id 刷请求不再线性增长（惰性 TTL 清理只覆盖被再次读取的 sid，
# 随机 sid 永不再读会永久驻留，必须有容量上限兜底）。
_SESSION_OWNERS: OrderedDict[str, tuple[str, float]] = OrderedDict()
_SESSION_OWNERS_MAX = 10_000
_OWNER_TTL_SECONDS = 24 * 3600.0
_ANON_SESSION_TTL_SECONDS = 3600.0


def _owner_ttl(owner: str) -> float:
    """owner 对应的绑定 TTL：匿名 1h，登录 24h。"""
    return _ANON_SESSION_TTL_SECONDS if owner == "anon" else _OWNER_TTL_SECONDS


def _bind_session_owner(sid: str, owner: str) -> None:
    """登记/升级 owner 绑定；仅容量达上限时触发清扫（稳态零开销）。"""
    if len(_SESSION_OWNERS) >= _SESSION_OWNERS_MAX:
        now = time.time()
        expired = [
            sid_
            for sid_, (owner_, bound_at) in _SESSION_OWNERS.items()
            if now - bound_at > _owner_ttl(owner_)
        ]
        for sid_ in expired:
            _SESSION_OWNERS.pop(sid_, None)
        while len(_SESSION_OWNERS) >= _SESSION_OWNERS_MAX:
            _SESSION_OWNERS.popitem(last=False)
    _SESSION_OWNERS[sid] = (owner, time.time())


def _verified_sub(request: Request) -> str | None:
    """验签后的 sub（T68 同款验签模式）；匿名/伪造 token 返回 None。"""
    auth = request.headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        payload = verify_token(auth[len("Bearer "):].strip())
        if payload and payload.get("sub"):
            return str(payload["sub"])
    return None


def _owner_of(sid: str) -> str | None:
    """读取 owner 绑定（含 TTL 过期清理：匿名 1h / 登录 24h）。"""
    entry = _SESSION_OWNERS.get(sid)
    if entry is None:
        return None
    owner, bound_at = entry
    if time.time() - bound_at > _owner_ttl(owner):
        _SESSION_OWNERS.pop(sid, None)
        return None
    return owner


def generate_session_id() -> str:
    """生成 ULID 字符串作为会话 ID（时间有序、26 字符 base32）。"""
    return str(ulid.new())


def get_session_id(request: Request) -> str:
    """读取请求头 X-Session-Id；缺失则生成新 ULID。

    - 显式截断至 64 字符上限，与 kb_search_logs.session_id VARCHAR(64) 对齐。
    - T77：登录态会话与 payload.sub 绑定（owner 归属）——显式提供的 session_id
      已绑定其他用户时拒绝（B 用户带 A 的 session_id 读不到 A 的历史）；
      匿名会话保持 ULID 弱保护并缩短 TTL（1h，内存条目过期自动失效）。
    """
    raw = request.headers.get("X-Session-Id")
    sid = raw.strip()[:64] if raw else generate_session_id()
    sub = _verified_sub(request)
    if sub:
        owner = _owner_of(sid)
        if owner is None or owner == "anon":
            _bind_session_owner(sid, sub)  # 首次绑定/匿名升级为登录（T102 有界写入）
        elif owner != sub:
            raise BizException(
                ErrorCode.A2002, "session 归属校验失败（owner 不匹配）"
            )
    elif raw:
        # 匿名显式 session_id：登记弱保护条目（TTL 1h 缩短）
        if _owner_of(sid) is None:
            _bind_session_owner(sid, "anon")
    return sid


def get_milvus_client() -> MilvusClient:
    global _milvus_client
    if _milvus_client is None:
        with _SINGLETON_LOCK:  # T127：DCL 双检，并发首调仅构造一次
            if _milvus_client is None:
                _milvus_client = MilvusClient()
    return _milvus_client


def get_es_client() -> ESClient:
    global _es_client
    if _es_client is None:
        with _SINGLETON_LOCK:  # T127：DCL 双检，并发首调仅构造一次
            if _es_client is None:
                _es_client = ESClient()
    return _es_client


async def get_embed_client() -> EmbedClient:
    global _embed_client
    if _embed_client is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _embed_client is None:  # T127：DCL 双检
                _embed_client = EmbedClient(redis=redis)
    return _embed_client


async def get_rerank_client() -> RerankClient:
    global _rerank_client
    if _rerank_client is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _rerank_client is None:  # T127：DCL 双检
                _rerank_client = RerankClient(redis=redis)
    return _rerank_client


async def get_ocr_client() -> OCRClient:
    global _ocr_client
    if _ocr_client is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _ocr_client is None:  # T127：DCL 双检
                _ocr_client = OCRClient(redis=redis)
    return _ocr_client


async def get_asr_client() -> ASRClient:
    global _asr_client
    if _asr_client is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _asr_client is None:  # T127：DCL 双检
                _asr_client = ASRClient(redis=redis)
    return _asr_client


# ===== V2 服务工厂 =====

async def get_rewrite_service() -> RewriteService:
    global _rewrite_service
    if _rewrite_service is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _rewrite_service is None:  # T127：DCL 双检
                _rewrite_service = RewriteService(redis=redis)
    return _rewrite_service


def get_routing_service() -> RoutingService:
    global _routing_service
    if _routing_service is None:
        with _SINGLETON_LOCK:  # T127：DCL 双检，并发首调仅构造一次
            if _routing_service is None:
                _routing_service = RoutingService()
    return _routing_service


def get_weighting_service() -> WeightingService:
    global _weighting_service
    if _weighting_service is None:
        with _SINGLETON_LOCK:  # T127：DCL 双检，并发首调仅构造一次
            if _weighting_service is None:
                _weighting_service = WeightingService()
    return _weighting_service


def get_row_access_service() -> RowAccessService:
    global _row_access_service
    if _row_access_service is None:
        with _SINGLETON_LOCK:  # T127：DCL 双检，并发首调仅构造一次
            if _row_access_service is None:
                _row_access_service = RowAccessService()
    return _row_access_service


async def get_session_service() -> SessionService:
    global _session_service
    if _session_service is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _session_service is None:  # T127：DCL 双检
                _session_service = SessionService(redis=redis)
    return _session_service


async def get_llm_service() -> LLMService:
    """V5：LLM 回答服务单例（开关门控 + 熔断 + 信号量 + 合规红线）。

    - word_provider：敏感词词库（红线关键词来源）——DB 优先 + 文件兜底
      （SensitiveWordsSyncService 既有逻辑），每次短 session 读取，
      LLMService 内部以 TTL 300s 缓存词集，不阻塞请求主链路；
      读取失败按空词集处理（LLM 回答仍可用，仅红线校验不生效并告警）。
    - T127 信号量留痕：LLMService.__init__ 内 self._sem =
      asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)（llm_service.py）。
      单例唯一后全进程仅一个 _sem 实例，并发获取不再产生 N 份信号量，
      并发检索信号量上限恒等于配置值（修复前实例分裂致上限 ×N 放大）。
    """
    global _llm_service
    if _llm_service is None:
        redis = await get_redis()  # T127：await 置于锁外，临界区内零 await
        with _SINGLETON_LOCK:
            if _llm_service is None:  # T127：DCL 双检，并发首调仅构造一次
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
    """从 Authorization 头提取 JWT payload；无 token 或校验失败返回 None（匿名）。

    搜索接口权限 ALL，不因 JWT 缺失/无效而 401；但匿名不等于放行全部：
    RowAccessService 在 payload=None 时只放行 ``KB_ANONYMOUS_CATEGORIES``
    白名单（默认 regulation），受限文档不进入检索结果（T21）。
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


# T78：上传流式分块大小——分块读取累计计数，超限提前中断（防内存 DoS）
_UPLOAD_CHUNK_SIZE = 1024 * 1024


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

    # T78：流式分块读取累计计数——超限提前中断（MUST NOT 全量读入内存后再校验）
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
                written += len(chunk)
                if written > max_size:
                    raise BizException(
                        ErrorCode.B0001,
                        f"上传文件超过大小上限 {max_size // (1024 * 1024)}MB",
                    )
                out.write(chunk)
    except Exception:
        dest.unlink(missing_ok=True)  # 部分写入残留清理
        raise
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
        # T78：流式分块读取累计计数——超限提前中断（MUST NOT 全量读入内存后再校验）
        written = 0
        with open(tmp.name, "wb") as out:
            while chunk := await file.read(_UPLOAD_CHUNK_SIZE):
                written += len(chunk)
                if written > max_size:
                    logger.info(
                        "upload_size_check_rejected",
                        filename=filename,
                        max_size=max_size,
                        actual_size=written,
                        check_source="streaming_read",
                    )
                    raise BizException(
                        ErrorCode.B0001,
                        f"上传文件超过大小上限 {max_size // (1024 * 1024)}MB",
                    )
                out.write(chunk)
        tmp.close()
        logger.info(
            "upload_size_check_passed",
            filename=filename,
            max_size=max_size,
            actual_size=written,
        )
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise
    return Path(tmp.name)

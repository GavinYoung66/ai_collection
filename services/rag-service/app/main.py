"""RAG Service FastAPI 入口。"""
import asyncio
import signal
import structlog
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router, probe_health
from app.api.documents import router as documents_router
from app.api.search import router as search_router
from app.api.sensitive_words import router as sensitive_words_router
from app.services.rate_limit import register_rate_limit_handler
from app.services.require_role import register_auth_handlers

setup_logging()
logger = structlog.get_logger()


def _hup_handler(signum, frame) -> None:
    """SIGHUP 热重载意图路由配置（不重启进程）。

    收到 SIGHUP 后取全局 routing_service 单例，重新 load(conf/intent_routes.yaml)，
    失败仅记录 error 不影响进程。对齐 §8.5 / README Reload 指引。
    """
    from app.api.deps import get_routing_service
    from app.services.routing_service import _DEFAULT_ROUTES_PATH

    try:
        routing_service = get_routing_service()
        routing_service.load(_DEFAULT_ROUTES_PATH)
        logger.info("routing_reloaded_via_sighup", path=str(_DEFAULT_ROUTES_PATH))
    except Exception as e:
        logger.error("routing_reload_failed", error=str(e))


async def _ensure_sensitive_words_baseline() -> None:
    """启动时敏感词 seed 完整性检查（§11.6）。

    - ① DB count(status='active') 查询；
    - ② count < 50 且 data/sensitive_words.dict 存在 → 逐行 INSERT ON CONFLICT DO NOTHING
      幂等补 seed（保障 Alembic 因 deploy 顺序极端异常未跑完时仍能补上 baseline）；
    - ③ 初始化 Redis rag:sensitive_words:version=1（key 不存在时设置，不设过期）；
    - ④ count < 50 → logger.warning 告警（不阻塞启动，运行期 get_current_dict 仍走 file fallback）。
    """
    from sqlalchemy import select, text

    from app.api.deps import get_redis
    from app.models.orm import SensitiveWord
    from app.services.sensitive_words_sync import (
        SensitiveWordsSyncService,
        _DICT_FILE,
        _MIN_DICT_SIZE,
        _VERSION_KEY,
    )
    from common.db.session import async_session_factory
    from common.trace.middleware import get_trace_id

    seeded = False
    count = 0
    redis = None
    async with async_session_factory() as session:
        # ① 查活跃敏感词数
        try:
            stmt = (
                select(SensitiveWord.word)
                .where(
                    SensitiveWord.status == "active",
                    SensitiveWord.is_delete == "0",
                )
            )
            result = await session.execute(stmt)
            count = len(result.scalars().all())
        except Exception as e:
            logger.warning(
                "sensitive_baseline_count_failed", error=str(e)
            )
            return

        # ② count < 50 且字典文件存在 → 逐行幂等补 seed
        if count < _MIN_DICT_SIZE and _DICT_FILE.exists():
            try:
                dict_text = await asyncio.to_thread(
                    _DICT_FILE.read_text, encoding="utf-8"
                )
            except Exception as e:
                logger.warning(
                    "sensitive_baseline_dict_read_failed",
                    path=str(_DICT_FILE),
                    error=str(e),
                )
                dict_text = ""

            for line in dict_text.splitlines():
                word = line.strip()
                if not word:
                    continue
                await session.execute(
                    text(
                        "INSERT INTO sensitive_words "
                        "(id, word, category, severity, status, description, "
                        "create_time, update_time, is_delete) "
                        "VALUES (uuid_generate_v4(), :word, 'threat', 'medium', "
                        "'active', 'seed_baseline_from_dict', NOW(), NOW(), '0') "
                        "ON CONFLICT (word) DO NOTHING"
                    ),
                    {"word": word},
                )
            await session.commit()

            # 重新计数（补 seed 后）
            try:
                result = await session.execute(stmt)
                count = len(result.scalars().all())
            except Exception as e:
                logger.warning(
                    "sensitive_baseline_recount_failed", error=str(e)
                )
            seeded = count >= _MIN_DICT_SIZE
            logger.info(
                "sensitive_baseline_seed_success",
                path=str(_DICT_FILE),
                count=count,
            )

    # ③ 初始化 Redis 版本号到 1（key 不存在时设置，不设过期）
    version = None
    try:
        redis = await get_redis()
        # SET key 1 NX → 仅当 key 不存在时设置；无 EX 即永不过期
        await redis.set(_VERSION_KEY, 1, nx=True)
        version = await redis.get(_VERSION_KEY)
    except Exception as e:
        logger.warning(
            "sensitive_baseline_version_init_failed", error=str(e)
        )

    # ③+ seed 实际执行了 INSERT（count 由 <50 → ≥50）→ 发布版本事件（B2）
    if seeded and redis is not None and version is not None:
        try:
            # D7 修复：seed 分支不再复用已关闭的旧 DB session 构造服务实例，
            # 改走不依赖 session 的静态方法路径（publish_version 仅需 Redis）
            await SensitiveWordsSyncService.publish_version(
                redis, int(version), get_trace_id() or "startup"
            )
        except Exception as e:
            logger.warning(
                "sensitive_baseline_publish_failed", error=str(e)
            )

    # ④ count < 50 告警（不阻塞启动），否则 info 记录最终 version
    if count < _MIN_DICT_SIZE:
        logger.warning("sensitive_baseline_below_threshold", count=count)
    else:
        logger.info(
            "sensitive_baseline_ok", count=count, version=version
        )

async def _resume_backfill_pending() -> None:
    """启动时扫描 `rag:backfill:pending` 集合，从 PG 回读文档重新调度 ES 补索引（5.9）。

    - 补索引任务随进程退出丢失，重启后由此恢复：成功/放弃由任务自身从集合移除；
    - 单条失败仅 warning，不阻塞其他条目与启动流程；
    - 补索引仅用 ES + Redis，直调模块级 backfill_es_index_task，不构造依赖 DB session 的
      IngestService（避免跨 session 使用隐患）。
    """
    import uuid
    from typing import Any, cast

    from sqlalchemy import select

    from app.api.deps import get_es_client, get_redis
    from app.models.orm import KBChunk
    from app.repositories.doc_repo import DocRepo
    from app.services.ingest_service import (
        _BACKFILL_PENDING_KEY,
        backfill_es_index_task,
    )
    from common.db.session import async_session_factory

    try:
        redis = await get_redis()
        # redis.asyncio 方法桩返回 Awaitable|T 联合类型，cast 消除 union
        pending = await cast(Any, redis).smembers(_BACKFILL_PENDING_KEY)
    except Exception as e:
        logger.warning("backfill_resume_scan_failed", error=str(e))
        return
    if not pending:
        return
    logger.warning("backfill_resume_found", count=len(pending))

    es = get_es_client()
    for doc_id_str in pending:
        try:
            uid = uuid.UUID(doc_id_str)
        except ValueError:
            await cast(Any, redis).srem(_BACKFILL_PENDING_KEY, doc_id_str)
            continue
        try:
            async with async_session_factory() as session:
                doc_repo = DocRepo(session)
                doc = await doc_repo.get_by_id(uid)
                if doc is None or doc.status != "active":
                    await cast(Any, redis).srem(_BACKFILL_PENDING_KEY, doc_id_str)
                    continue
                result = await session.execute(
                    select(KBChunk).where(
                        KBChunk.doc_id == uid, KBChunk.is_delete == "0"
                    )
                )
                chunks = result.scalars().all()
                if not chunks:
                    await cast(Any, redis).srem(_BACKFILL_PENDING_KEY, doc_id_str)
                    continue
                es_docs = [
                    {
                        "chunk_id": str(c.id),
                        "content": c.content,
                        "doc_id": doc_id_str,
                        "category": c.category,
                        "doc_summary": doc.summary or "",
                    }
                    for c in chunks
                ]
            # DB session 已随 async with 退出关闭；补索引仅用 ES + Redis
            asyncio.create_task(backfill_es_index_task(es, redis, es_docs, uid))
        except Exception as e:
            logger.warning(
                "backfill_resume_item_failed", doc_id=doc_id_str, error=str(e)
            )


app = FastAPI(title="RAG Service", version="1.0.0")
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)

app.include_router(health_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1/kb/documents")
app.include_router(search_router, prefix="/api/v1/kb")
app.include_router(sensitive_words_router, prefix="/api/v1")
register_rate_limit_handler(app)
register_auth_handlers(app)


def wire_resilience_hooks() -> None:
    """把 common.resilience.hooks 接到 rag 适配器（记录 rag.*，不进投诉六频道）。

    shutdown 必须 set_publisher(None)，避免测试间泄漏。
    """
    from app.events.resilience import on_resilience_event
    from common.resilience import hooks

    hooks.set_publisher(on_resilience_event)


def _unwire_resilience_hooks() -> None:
    from common.resilience import hooks

    hooks.set_publisher(None)


def wire_resilience_redis() -> None:
    """把 rag Redis（RAG_REDIS_DB）注入 common 零件，与限流共用连接池。"""
    from app.services.rate_limit import _ensure_common_client

    _ensure_common_client()


@app.on_event("startup")
async def startup():
    """启动时注入韧性接线，再初始化 Milvus collection + ES index。"""
    from common.llm.reliability import set_default_service
    from app.services.search_trace import configure_langfuse_env, configure_langsmith_env

    set_default_service("rag")
    wire_resilience_hooks()
    wire_resilience_redis()

    configure_langsmith_env()
    configure_langfuse_env()
    from app.api.deps import get_es_client, get_milvus_client

    # 注册 SIGHUP 热重载（意图路由配置 reload，不重启进程，对齐 §8.5）
    # signal.signal 仅主线程可用；uvicorn 主线程即 startup 所在线程
    signal.signal(signal.SIGHUP, _hup_handler)
    logger.info("sighup_handler_registered")

    milvus = get_milvus_client()
    try:
        await milvus.ensure_collection()
        logger.info("milvus_initialized")
    except Exception as e:
        logger.warning("milvus_init_failed", error=str(e))

    es = get_es_client()
    try:
        await es.ensure_index()
        logger.info("es_initialized")
    except Exception as e:
        logger.warning("es_init_failed", error=str(e))

    # 敏感词 seed 完整性检查 + Redis 版本号初始化（§11.6，不阻塞启动）
    try:
        await _ensure_sensitive_words_baseline()
    except Exception as e:
        logger.warning("sensitive_baseline_init_failed", error=str(e))

    # ES 降级补索引恢复（5.9）：扫描 rag:backfill:pending 集合，重新调度待补任务
    try:
        await _resume_backfill_pending()
    except Exception as e:
        logger.warning("backfill_resume_init_failed", error=str(e))

    # 异步入库僵尸 processing 回收（async-kb-ingest-observability）
    try:
        await _sweep_stale_ingest_jobs()
    except Exception as e:
        logger.warning("ingest_sweep_init_failed", error=str(e))


async def _sweep_stale_ingest_jobs() -> None:
    """启动时将超时 processing 文档置 failed，避免进程重启丢 task 后永久卡住。"""
    from app.settings import settings
    from common.db.session import async_session_factory
    from app.repositories.doc_repo import DocRepo

    async with async_session_factory() as session:
        count = await DocRepo(session).sweep_stale_processing(
            settings.KB_INGEST_JOB_TIMEOUT
        )
        if count:
            await session.commit()
            logger.warning("ingest_stale_processing_swept", count=count)


@app.on_event("shutdown")
async def shutdown():
    """幂等关闭全局客户端（embed/rerank/milvus/es/redis）。

    - 各客户端 close 已实现幂等；单点失败仅 warning，不影响其余关闭。
    """
    from app.api.deps import (
        _embed_client,
        _es_client,
        _milvus_client,
        _redis_client,
        _rerank_client,
    )

    if _es_client is not None:
        try:
            await _es_client.close()
        except Exception as e:
            logger.warning("shutdown_es_close_failed", error=str(e))
    if _embed_client is not None:
        try:
            await _embed_client.close()
        except Exception as e:
            logger.warning("shutdown_embed_close_failed", error=str(e))
    if _rerank_client is not None:
        try:
            await _rerank_client.close()
        except Exception as e:
            logger.warning("shutdown_rerank_close_failed", error=str(e))
    if _milvus_client is not None:
        try:
            await _milvus_client.close()
        except Exception as e:
            logger.warning("shutdown_milvus_close_failed", error=str(e))
    if _redis_client is not None:
        try:
            await _redis_client.aclose()
        except Exception as e:
            logger.warning("shutdown_redis_close_failed", error=str(e))
    _unwire_resilience_hooks()


@app.get("/health")
async def health():
    """根路径 /health：docker-compose healthcheck 依赖此路径，与 /api/v1/health 共用同一探活实现。"""
    return await probe_health()

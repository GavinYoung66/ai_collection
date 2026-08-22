"""RAG Service FastAPI 入口。"""
import asyncio
import signal
import structlog
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router
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
        _DICT_FILE,
        _MIN_DICT_SIZE,
        _VERSION_KEY,
    )
    from common.db.session import async_session_factory

    count = 0
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
            logger.info(
                "sensitive_baseline_seeded",
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

    # ④ count < 50 告警（不阻塞启动），否则 info 记录最终 version
    if count < _MIN_DICT_SIZE:
        logger.warning("sensitive_baseline_below_threshold", count=count)
    else:
        logger.info(
            "sensitive_baseline_ok", count=count, version=version
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


@app.on_event("startup")
async def startup():
    """启动时初始化 Milvus collection + ES index。"""
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


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "rag-service", "version": "1.0.0"}

"""RAG Service FastAPI 入口。"""
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
    from app.vectorstore.es_client import ESClient
    from app.vectorstore.milvus_client import MilvusClient

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


@app.get("/health")
async def health():
    return {"status": "healthy", "service": "rag-service", "version": "1.0.0"}

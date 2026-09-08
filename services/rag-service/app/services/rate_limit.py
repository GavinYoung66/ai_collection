"""接口限流组件：固定窗口计数（Redis INCR + EXPIRE），路由层 Depends 拦截。

- 按用户维度计数：优先读取 `X-User-Id` 请求头，缺失时回退客户端 IP；
- 超过阈值抛 `RateLimitExceeded`，由 `register_rate_limit_handler` 统一转为
  HTTP 429 + 项目统一响应包 `{code, message, data, trace_id}`；
- Redis 任何异常静默放行（限流降级，不阻断业务可用性）。
"""
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.settings import settings
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

_RATE_KEY_PREFIX = "rag:ratelimit:"
_RATE_LIMIT_CODE = "B0006"
_RATE_LIMIT_MESSAGE = "请求过于频繁，请稍后再试"

_redis: Redis | None = None


class RateLimitExceeded(Exception):
    """限流触发异常，由注册的 handler 统一转为 429 响应。"""

    def __init__(self, message: str = _RATE_LIMIT_MESSAGE):
        self.message = message
        super().__init__(message)


def _client() -> Redis:
    """独立连接池（带短超时），与业务 Redis 客户端隔离，防挂起拖慢请求。"""
    global _redis
    if _redis is None:
        _redis = Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.RAG_REDIS_DB,
            decode_responses=True,
            socket_timeout=1.0,
            socket_connect_timeout=1.0,
        )
    return _redis


def _user_key(request: Request) -> str:
    """按用户维度：X-User-Id 优先，缺失回退客户端 IP。"""
    user = request.headers.get("X-User-Id")
    if not user:
        user = request.client.host if request.client else "anonymous"
    return user


def rate_limiter(bucket: str, limit: int, window: int = 60) -> Callable:
    """返回 FastAPI 依赖：固定窗口内计数，超过 limit 抛 RateLimitExceeded。

    - bucket: 接口桶标识（如 kb_upload），不同接口独立计数；
    - limit: 窗口内允许的最大请求数；
    - window: 窗口秒数，默认 60。
    """

    async def check(request: Request) -> None:
        key = f"{_RATE_KEY_PREFIX}{bucket}:{_user_key(request)}"
        try:
            count = await _client().incr(key)
            if count == 1:
                await _client().expire(key, window)
            if count > limit:
                raise RateLimitExceeded()
        except RateLimitExceeded:
            raise
        except Exception as e:  # Redis 故障静默放行，限流降级不阻断业务
            logger.warning("rate_limit_check_failed", bucket=bucket, error=str(e))

    return check


def register_rate_limit_handler(app: FastAPI) -> None:
    """注册限流异常处理器：429 + 统一响应包。"""

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        logger.warning("rate_limit_exceeded", message=exc.message, trace_id=get_trace_id())
        return JSONResponse(
            status_code=429,
            content={
                "code": _RATE_LIMIT_CODE,
                "message": exc.message,
                "data": None,
                "trace_id": get_trace_id(),
            },
        )

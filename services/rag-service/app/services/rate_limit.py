"""接口限流组件：固定窗口计数（委托 common.resilience.rate_limit），路由层 Depends 拦截。

- 按用户维度计数：优先读取 `X-User-Id` 请求头，缺失时回退客户端 IP；
- 超过阈值抛 `RateLimitExceeded`，由 `register_rate_limit_handler` 统一转为
  HTTP 429 + 项目统一响应包 `{code, message, data, trace_id}`；
- Redis 任何异常静默放行（限流降级，不阻断业务可用性）——降级方向由
  common.resilience.rate_limit.allow 保证（Redis 挂时返回 True）。

迁移说明（platform-resilience-primitives 5.1）：计数逻辑委托 common 零件
（INCR + 首次 EXPIRE + Redis 故障静默放行），本文件保留 rag 独立连接池
（RAG_REDIS_DB，与业务 Redis 隔离）并经 configure_redis 注入 common 共享客户端，
键名 `rag:ratelimit:{bucket}:{user}`、阈值参数与 429/B0006 响应契约均不变。
"""
from collections.abc import Callable

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from redis.asyncio import Redis

from app.settings import settings
from common.resilience.rate_limit import allow as _rate_limit_allow
from common.resilience._redis import configure_redis as _configure_common_redis
from common.security.rbac import verify_token
from common.trace.middleware import get_trace_id
from common.utils.ip import get_client_ip

logger = structlog.get_logger()

_RATE_KEY_PREFIX = "rag:ratelimit:"
_RATE_LIMIT_CODE = "B0006"
_RATE_LIMIT_MESSAGE = "请求过于频繁，请稍后再试"

_redis: Redis | None = None
_injected: Redis | None = None


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


def _ensure_common_client() -> Redis:
    """把 rag 连接池（或测试 fake）注入 common 限流零件，实例变更时重注入。"""
    global _injected
    client = _client()
    if _injected is not client:
        _configure_common_redis(client)
        _injected = client
    return client


def _user_key(request: Request) -> str:
    """T68：限流身份取服务端可信来源——验签后 sub 优先（``uid:`` 命名空间），
    匿名回退客户端 IP（``ip:`` 命名空间）。

    - 两命名空间 MUST NOT 混用（spec §6.7）；
    - ``X-User-Id`` 请求头不再进限流 key（伪造 100 个不同头会膨胀 Redis key
      空间，且维度可被客户端操控）；
    - token 经 verify_token 验签（含 T69 的 type 校验），伪造/refresh token
      一律落入 ``ip:`` 命名空间。
    """
    auth = request.headers.get("authorization") or ""
    payload = None
    if auth.startswith("Bearer "):
        payload = verify_token(auth[len("Bearer "):].strip())
    if payload and payload.get("sub"):
        return f"uid:{payload['sub']}"
    ip = get_client_ip(request) or "anonymous"
    return f"ip:{ip}"


def rate_limiter(bucket: str, limit: int, window: int = 60) -> Callable:
    """返回 FastAPI 依赖：固定窗口内计数，超过 limit 抛 RateLimitExceeded。

    - bucket: 接口桶标识（如 kb_upload），不同接口独立计数；
    - limit: 窗口内允许的最大请求数；
    - window: 窗口秒数，默认 60。
    """

    async def check(request: Request) -> None:
        _ensure_common_client()
        key = f"{_RATE_KEY_PREFIX}{bucket}:{_user_key(request)}"
        allowed = await _rate_limit_allow(
            key, limit, window, context={"trace_id": get_trace_id()}
        )
        if not allowed:
            raise RateLimitExceeded()

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

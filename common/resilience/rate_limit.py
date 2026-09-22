"""固定窗口限流原语：崩溃安全的 Redis ``SET NX EX`` + ``INCR`` 计数。

- ``allow(key, limit, window)`` 窗口内计数超过 limit 返回 False，由调用方决定
  拒绝动作（如抛 B0006，由服务异常处理器转 HTTP 429）；
- Redis 不可用/超时时静默放行（限流降级不阻断业务可用性），仅 warning 日志；
- 短 socket 超时（见 ``_redis.get_redis``），Redis 挂起不会拖住调用方。

计数顺序刻意为「先 ``SET key 0 NX EX window`` 再 ``INCR``」，而非旧版
``INCR`` 成功后补 ``EXPIRE``：旧版在两命令间连接断开时会留下**无 TTL 计数键**，
Redis 恢复后该维度被永久限流直到人工删 key。新版 ``SET NX`` 失败即整体降级放行，
不会产生裸计数键；INCR 不清除既有 TTL；两命令间崩溃最坏残留值为 "0" 的带 TTL 键，
下一窗口自动过期。

迁移自 rag-service 固定窗口口径，固定窗口边界可能有 2 倍突刺，P0 与现网一致接受。
"""
from __future__ import annotations

import structlog
from redis.exceptions import RedisError

from common.resilience._redis import get_redis
from common.resilience.hooks import on_event

logger = structlog.get_logger()


async def allow(
    key: str,
    limit: int,
    window: int = 60,
    *,
    context: dict | None = None,
) -> bool:
    """窗口内是否放行。

    - ``SET key 0 NX EX window`` 保证计数键必有 TTL（键已存在则不动其 TTL），
      再 INCR：计数从 1 开始，limit=20 时第 21 次请求被拒；
    - 拒绝（False）时发 ``{svc}.rate_limit.hit`` 事件，context 中的
      trace_id/node 业务字段 best-effort 透传；
    - Redis 故障返回 True（静默放行）并发 ``{svc}.rate_limit.degraded`` 事件，
      供服务订阅做事后告警。
    """
    try:
        client = get_redis()
        await client.set(key, "0", ex=window, nx=True)
        count = await client.incr(key)
        allowed = count <= limit
        if not allowed:
            payload: dict = {"key": key, "limit": limit, "reason": "rate_limited"}
            if context:
                for field_name in ("trace_id", "node", "code"):
                    value = context.get(field_name)
                    if value:
                        payload[field_name] = value
            await _emit(key, "hit", payload)
        return allowed
    except (RedisError, OSError) as e:
        logger.warning("rate_limit_degraded_allow", key=key, error=str(e))
        await _emit(key, "degraded", {"key": key, "error": str(e)})
        return True


async def _emit(key: str, action: str, payload: dict) -> None:
    """发限流事件；svc 取 key 首段。事件上报失败绝不影响限流主流程。"""
    service = key.split(":", 1)[0]
    try:
        await on_event(f"{service}.rate_limit.{action}", payload)
    except Exception as e:  # noqa: BLE001
        logger.warning("rate_limit_hook_failed", key=key, error=str(e))

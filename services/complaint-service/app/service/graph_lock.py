"""跨进程图执行锁（多副本安全）。

**为什么不用 `common.resilience.idempotency.acquire`**：它是 fail-closed
（Redis 不可用时返回 False = "未获得锁"），拿来做图锁会让 **Redis 一抖动
所有研判停摆**。而这里锁的语义是「防重复执行」，不是「保证正确性」——
拿不到锁最坏是多跑一次图（有幂等回流兜底），让服务整体不可用则不划算。
故本模块 Redis 故障时返回 `LOCK_UNAVAILABLE`，由调用方退化为「仅进程内守卫」。

与进程内 `_graph_inflight` / `_resume_locks` 的分工：
- 进程内结构：快速路径，零 Redis 往返，覆盖单副本的全部场景；
- 本模块：仅在多副本（`--scale > 1`）时才有额外价值，用于跨进程互斥。

释放采用 token 比对（Lua 原子 CAS），避免「A 的锁过期 → B 拿到 → A 释放时
误删 B 的锁」这一经典竞态。
"""
from __future__ import annotations

import uuid
from typing import Optional

import structlog
from redis.exceptions import RedisError

from app.utils.redis_keys import (
    graph_lock_key,
    maintenance_lock_key,
    resume_lock_key,
)
from common.resilience._redis import get_redis

logger = structlog.get_logger()

# 三态：调用方必须区分「他人持有」与「Redis 不可用」，二者处理方式相反
LOCK_ACQUIRED = "acquired"        # 本进程拿到锁
LOCK_HELD = "held"                # 他人持有 → 应跳过
LOCK_UNAVAILABLE = "unavailable"  # Redis 不可用 → 退化为仅进程内守卫

# 原子「比对 token 再删除」，避免误删他人锁
_RELEASE_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""

# 原子「比对 token 再续期」——只延长自己持有的锁
_EXTEND_LUA = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def new_token() -> str:
    """锁持有凭证。每次获取生成一个，释放时比对。"""
    return uuid.uuid4().hex


async def _acquire(key: str, ttl: int, token: str) -> str:
    try:
        client = get_redis()
        ok = await client.set(key, token, nx=True, ex=max(1, int(ttl)))
        return LOCK_ACQUIRED if ok else LOCK_HELD
    except (RedisError, OSError) as e:
        logger.warning("graph_lock_unavailable", key=key, error=str(e))
        return LOCK_UNAVAILABLE


async def _release(key: str, token: str) -> None:
    try:
        client = get_redis()
        script = getattr(client, "eval", None)
        if script is None:
            # 无 eval（极简测试替身）：退化为 GET 比对再删。
            # 非原子，但**仍然做 token 校验**——绝不退化成盲目 DELETE，
            # 否则会误删他人的锁（A 的锁过期后 B 拿到，A 释放时删掉 B 的）。
            current = await client.get(key)
            if current is None or _as_str(current) == token:
                await client.delete(key)
            return
        await client.eval(_RELEASE_LUA, 1, key, token)
    except (RedisError, OSError) as e:
        # 释放失败无需处理：TTL 会兜底，最坏是这段时间内他人无法获取
        logger.warning("graph_lock_release_failed", key=key, error=str(e))


def _as_str(value: object) -> str:
    """redis 客户端 decode_responses 配置不同，返回值可能是 bytes。"""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


async def acquire_graph_lock(complaint_id: str, ttl: int, token: Optional[str] = None) -> str:
    """获取图执行锁。ttl 应覆盖一次图执行的最长耗时（对齐 exec_timeout）。"""
    return await _acquire(graph_lock_key(complaint_id), ttl, token or new_token())


async def release_graph_lock(complaint_id: str, token: str) -> None:
    await _release(graph_lock_key(complaint_id), token)


async def acquire_resume_lock(complaint_id: str, ttl: int, token: Optional[str] = None) -> str:
    """获取 resume 互斥锁。ttl 应覆盖 resume 的轮询等待上限。"""
    return await _acquire(resume_lock_key(complaint_id), ttl, token or new_token())


async def release_resume_lock(complaint_id: str, token: str) -> None:
    await _release(resume_lock_key(complaint_id), token)


async def acquire_maintenance_lock(ttl: int, token: Optional[str] = None) -> str:
    """获取周期维护任务锁（跨副本互斥，防多副本重复执行超时转人工/三表裁剪）。

    ttl 应略大于一次维护循环的耗时（秒级即可）。fail-open：Redis 不可用时返回
    LOCK_UNAVAILABLE，调用方继续执行（维护任务幂等，双跑无害）。
    """
    return await _acquire(maintenance_lock_key(), ttl, token or new_token())


async def release_maintenance_lock(token: str) -> None:
    await _release(maintenance_lock_key(), token)


async def extend_resume_lock(complaint_id: str, ttl: int, token: str) -> bool:
    """把自己持有的 resume 锁续期，返回是否续期成功。

    为什么需要：resume 分两段——**轮询等断点**（最长 _RESUME_WAIT_TIMEOUT_S）
    和**执行 Command(resume)**（内含 sample_backflow 的 MCP 调用，可能数十秒）。
    若用单一 TTL 覆盖两段，锁可能在执行段中途过期，另一副本随即拿到锁并双跑
    —— 恰是本锁要防的事。故进入断点后、执行 resume 前显式续期为执行超时量级。
    """
    try:
        client = get_redis()
        script = getattr(client, "eval", None)
        key = resume_lock_key(complaint_id)
        if script is None:
            current = await client.get(key)
            if current is None or _as_str(current) != token:
                return False
            await client.expire(key, max(1, int(ttl)))
            return True
        result = await client.eval(_EXTEND_LUA, 1, key, token, max(1, int(ttl)))
        return bool(result)
    except (RedisError, OSError) as e:
        logger.warning("graph_lock_extend_failed", complaint_id=complaint_id, error=str(e))
        return False

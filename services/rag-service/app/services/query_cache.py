"""检索查询缓存切面：读/写/按 category 失效，全部静默容错。

- cache key: rag:cache:query:{md5(query+category+top_k)}
- 每个 category 用 Redis Set（rag:cache:keys:{category}）维护关联 cache key，
  失效时遍历 Set 删除，禁止使用 KEYS 通配扫描。
- Redis 任何异常均静默绕过缓存，绝不抛业务异常。
"""
import hashlib
import json

import structlog
from redis.asyncio import Redis

from common.config.settings import settings

logger = structlog.get_logger()

_CACHE_KEY_PREFIX = "rag:cache:query:"
_INDEX_KEY_PREFIX = "rag:cache:keys:"

_redis: Redis | None = None


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


def make_cache_key(query: str, category: str | None, top_k: int) -> str:
    """md5 由 query + category + top_k 拼接字符串生成。"""
    raw = f"{query}{category or ''}{top_k}"
    return _CACHE_KEY_PREFIX + hashlib.md5(raw.encode("utf-8")).hexdigest()


def _index_key(category: str | None) -> str:
    return _INDEX_KEY_PREFIX + (category or "")


async def get_cached(key: str) -> dict | None:
    """命中返回缓存的完整对外响应 dict；未命中/异常返回 None。"""
    try:
        raw = await _client().get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("query_cache_get_failed", error=str(e))
        return None


async def set_cached(
    key: str, response: dict, category: str | None, ttl: int | None = None
) -> None:
    """写入缓存（TTL=KB_CACHE_TTL）并登记到 category 的 key Set。"""
    try:
        client = _client()
        await client.setex(
            key, ttl or settings.KB_CACHE_TTL, json.dumps(response, ensure_ascii=False)
        )
        await client.sadd(_index_key(category), key)
    except Exception as e:
        logger.warning("query_cache_set_failed", error=str(e))


async def invalidate_category(category: str | None) -> None:
    """失效指定 category 的全部查询缓存（同时失效无 category 的全局桶）。

    遍历 Set 删除关联 key，禁止 KEYS 通配扫描；异常静默。
    """
    try:
        client = _client()
        buckets = {_index_key(category), _index_key("")}
        for index_key in buckets:
            keys = await client.smembers(index_key)
            if keys:
                await client.delete(*keys)
            await client.delete(index_key)
    except Exception as e:
        logger.warning("query_cache_invalidate_failed", error=str(e))

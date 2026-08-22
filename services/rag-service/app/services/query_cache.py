"""检索查询缓存切面：读/写/按 category 失效，全部静默容错。

- V2 cache key: rag:cache:query:v2:{md5(query+category+top_k+flags_hash)}
  flags_hash 为 7 个 V2 开关状态 hash，开关变化时缓存隔离，避免 V1 旧 key 冲突。
  V1 旧 key（rag:cache:query:{md5}）不做任何读写，自然过期。
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

_CACHE_KEY_PREFIX = "rag:cache:query:v2:"
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


def _flags_hash() -> str:
    """7 个 V2 开关状态 hash（8 字符），开关变化时缓存隔离。"""
    flags = "|".join([
        str(settings.KB_QUERY_REWRITE_ENABLED),
        str(settings.KB_ROUTING_ENABLED),
        str(settings.KB_WEIGHTING_ENABLED),
        str(settings.KB_ROW_LEVEL_ENABLED),
        str(settings.KB_MULTITURN_ENABLED),
        str(settings.KB_EMBED_DEGRADED_KEEP_TEXT),
        str(settings.ES_DUAL_WRITE),
    ])
    return hashlib.md5(flags.encode("utf-8")).hexdigest()[:8]


def make_cache_key(query: str, category: str | None, top_k: int) -> str:
    """V2 cache key: rag:cache:query:v2:{md5(query+category+top_k+flags_hash)}。

    flags_hash 随 7 个开关状态变化，开关切换后旧缓存自动隔离不命中。
    """
    raw = f"{query}{category or ''}{top_k}{_flags_hash()}"
    return _CACHE_KEY_PREFIX + hashlib.md5(raw.encode("utf-8")).hexdigest()


def is_cacheable(result: dict) -> bool:
    """检索响应是否可写缓存（V1-T19）。

    兜底/降级结果不缓存：degraded=true（降级路径）或 results 为空且带兜底文案
    （无匹配兜底），否则故障/无匹配结果会被缓存 1 小时，知识库更新后仍返回旧结果。
    """
    if result.get("degraded"):
        return False
    if not result.get("results") and result.get("message"):
        return False
    return True


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

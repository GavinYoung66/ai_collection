"""检索查询缓存切面：读/写/按 category 失效，全部静默容错。

- V2 cache key: rag:cache:query:v2:{md5(query+category+top_k+flags_hash+scope)}
  flags_hash 为功能开关状态 hash（V2 收录 7 个开关，V4 追加 KB_SUMMARY_ENABLED，
  T21 追加 KB_ANONYMOUS_CATEGORIES），开关变化时缓存隔离，避免 V1 旧 key 冲突。
- T21：cache key 追加身份作用域 scope——匿名请求固定 ``anon``，行级权限开启时
  登录用户按 (tenant_id, role, department) 哈希隔离；否则匿名与登录用户会共享缓存，
  导致受限文档经由缓存泄漏给未带身份的请求。
  V1 旧 key（rag:cache:query:{md5}）不做任何读写，自然过期。
- 每个 category 用 Redis Set（rag:cache:keys:{category}）维护关联 cache key，
  失效时遍历 Set 删除，禁止使用 KEYS 通配扫描。
- Redis 任何异常均静默绕过缓存，绝不抛业务异常。
"""
import hashlib
import json

import structlog
from redis.asyncio import Redis

from app.settings import settings

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
    """功能开关状态 hash（8 字符），开关变化时缓存隔离。

    V2 收录 7 个开关；V4 追加 KB_SUMMARY_ENABLED：摘要增强影响 ES doc_summary 加权与
    响应 results[].summary 填充，开关切换若不隔离缓存，旧缓存（summary=null/已填充）
    会在 1h TTL 内违反"开关开启填充摘要/关闭恒为 null"契约（spec summary-metadata 3.6/3.7）。
    T21 追加 KB_ANONYMOUS_CATEGORIES：匿名白名单收紧后旧匿名缓存可能仍含受限文档，
    必须随配置变化隔离。
    """
    flags = "|".join([
        str(settings.KB_QUERY_REWRITE_ENABLED),
        str(settings.KB_ROUTING_ENABLED),
        str(settings.KB_WEIGHTING_ENABLED),
        str(settings.KB_ROW_LEVEL_ENABLED),
        str(settings.KB_MULTITURN_ENABLED),
        str(settings.KB_EMBED_DEGRADED_KEEP_TEXT),
        str(settings.ES_DUAL_WRITE),
        # V4: 摘要元数据检索增强开关
        str(settings.KB_SUMMARY_ENABLED),
        # T21: 匿名可见类别白名单（收紧后必须隔离旧缓存）
        str(getattr(settings, "KB_ANONYMOUS_CATEGORIES", "regulation")),
    ])
    return hashlib.md5(flags.encode("utf-8")).hexdigest()[:8]


def identity_scope(
    jwt_payload: dict | None, row_level_enabled: bool = False
) -> str | None:
    """缓存身份作用域（T21）。

    - 匿名（payload=None）→ ``anon``：与登录用户缓存完全隔离；
    - 行级权限开启 → 按 (tenant_id, role, department) 哈希隔离，不同可见集合不共享缓存；
    - 行级权限关闭 → ``None``（登录用户共享 V2 共享 key，与 V3 缓存行为一致）。
    """
    if jwt_payload is None:
        return "anon"
    if not row_level_enabled:
        return None
    role = str(jwt_payload.get("role") or "default").upper()
    dept = str(jwt_payload.get("department") or "default")
    if dept.upper() == "UNKNOWN":
        dept = "default"
    tenant = str(jwt_payload.get("tenant_id") or "")
    digest = hashlib.md5(f"{tenant}|{role}|{dept}".encode("utf-8")).hexdigest()[:12]
    return f"u:{digest}"


def make_cache_key(
    query: str, category: str | None, top_k: int, scope: str | None = None
) -> str:
    """    V2 cache key: rag:cache:query:v2:{md5(len(query)+":"+query+category+top_k+flags_hash+scope)}。

    T75：query 段加长度前缀（spec §6.5 钉死）——消除拼接歧义（如 query="ab"+
    category="c" 与 query="abc"+category=None 的旧 key 相同导致串扰）；旧 key 由
    TTL 自然过期失效，无需迁移。

    flags_hash 随功能开关状态变化（V2 7 个 + V4 1 个 + T21 1 个），开关切换后旧缓存
    自动隔离不命中；scope 为身份作用域（见 ``identity_scope``），匿名固定 ``anon``。
    """
    raw = f"{len(query)}:{query}|{category or ''}|{top_k}|{_flags_hash()}|{scope or ''}"
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

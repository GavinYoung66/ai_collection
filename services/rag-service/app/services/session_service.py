"""多轮会话上下文记录：Redis LPUSH + LTRIM + EXPIRE 原子管道。

- key: rag:session:v2:{session_id}
- 每轮记录 {query, rewritten_query, top_doc_ids, ts}，最多保留 SESSION_HISTORY_CAP 轮；
- 写入失败静默（仅 warning），绝不阻断检索主流程；
- 读取失败/空会话返回空列表，视为正常降级（已过期/无上下文）。
"""
import json
import time

import structlog
from redis.asyncio import Redis

from app.settings import settings

logger = structlog.get_logger()

_SESSION_KEY_PREFIX = "rag:session:v2:"


class SessionService:
    """多轮会话上下文服务：记录检索轮次并回看最近 N 轮原始 query。"""

    def __init__(self, redis: Redis):
        self.redis = redis

    async def record(
        self,
        session_id: str,
        query: str,
        rewritten_query: str | None,
        top_doc_ids: list[str],
    ) -> None:
        """记录一轮会话上下文（LPUSH + LTRIM + EXPIRE 原子管道）。

        - 单次 pipeline 原子执行：LPUSH 写入 → LTRIM 截断至 SESSION_HISTORY_CAP → EXPIRE 刷新 TTL；
        - 任何异常仅 warning，不抛出，保证检索主链路不受会话记录影响。
        """
        key = f"{_SESSION_KEY_PREFIX}{session_id}"
        payload = json.dumps(
            {
                "query": query,
                "rewritten_query": rewritten_query,
                "top_doc_ids": top_doc_ids,
                "ts": int(time.time()),
            },
            ensure_ascii=False,
        )
        try:
            pipe = self.redis.pipeline()
            pipe.lpush(key, payload)
            pipe.ltrim(key, 0, settings.SESSION_HISTORY_CAP - 1)
            pipe.expire(key, settings.SESSION_TTL_SECONDS)
            await pipe.execute()
        except Exception as e:
            logger.warning(
                "session_record_failed", session_id=session_id, error=str(e)
            )

    async def get_recent_queries(self, session_id: str, n: int) -> list[str]:
        """返回最近 n 轮原始 query，按 old→new 顺序。

        - LRANGE 0 n-1 取头部 n 条（LPUSH 后最新在头部，即 new→old）；
        - 解析每条 JSON 取 query 字段，反转得到 old→new；
        - 连接异常 / 空会话 / 已过期：返回 []，视为正常降级，不抛异常。
        """
        key = f"{_SESSION_KEY_PREFIX}{session_id}"
        try:
            raw_entries = await self.redis.lrange(key, 0, n - 1)
        except Exception as e:
            logger.warning(
                "session_get_failed", session_id=session_id, error=str(e)
            )
            return []

        queries: list[str] = []
        for raw in raw_entries:
            try:
                item = json.loads(raw)
                q = item.get("query")
                if q:
                    queries.append(q)
            except Exception as e:
                logger.warning(
                    "session_entry_parse_failed",
                    session_id=session_id,
                    error=str(e),
                )
                continue
        # LPUSH 顺序为 new→old，反转为 old→new
        queries.reverse()
        return queries

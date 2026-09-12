"""演示待检登记与时间线：Redis DB 3。禁止用 DP pending-qc 当演示列表。"""
from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

DEMO_SESSIONS_KEY = "qc:demo:sessions"
TRACE_KEY_PREFIX = "qc:demo:trace:"
TRACE_TTL_SECONDS = 86400


def _assert_redis_db3(redis: Redis, *, label: str) -> None:
    pool = getattr(redis, "connection_pool", None)
    kwargs = getattr(pool, "connection_kwargs", None) if pool is not None else None
    if isinstance(kwargs, dict):
        db = kwargs.get("db")
        if db != 3:
            raise RuntimeError(f"{label} must use Redis DB 3, got {db!r}")


def trace_events_key(session_id: str) -> str:
    return f"{TRACE_KEY_PREFIX}{session_id}"


def trace_meta_key(session_id: str) -> str:
    return f"{TRACE_KEY_PREFIX}{session_id}:meta"


class DemoSessionStore:
    def __init__(self, redis: Redis):
        self._redis = redis

    def _assert_db3(self) -> None:
        _assert_redis_db3(self._redis, label="demo session store")

    async def add_many(self, items: list[dict[str, Any]]) -> None:
        self._assert_db3()
        existing = await self.list_all()
        existing.extend(items)
        await self._redis.set(DEMO_SESSIONS_KEY, json.dumps(existing, ensure_ascii=False))

    async def list_all(self) -> list[dict[str, Any]]:
        self._assert_db3()
        raw = await self._redis.get(DEMO_SESSIONS_KEY)
        if raw is None:
            return []
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [item for item in data if isinstance(item, dict)]


class DemoTraceStore:
    """管道各步 rpush 到 qc:demo:trace:{session_id}，TTL 24h。"""

    def __init__(self, redis: Redis):
        self._redis = redis

    def _assert_db3(self) -> None:
        _assert_redis_db3(self._redis, label="demo trace store")

    async def append(self, session_id: str, event: dict[str, Any]) -> None:
        self._assert_db3()
        key = trace_events_key(session_id)
        await self._redis.rpush(key, json.dumps(event, ensure_ascii=False))
        await self._redis.expire(key, TRACE_TTL_SECONDS)

    async def list_events(self, session_id: str) -> list[dict[str, Any]]:
        self._assert_db3()
        raw_items = await self._redis.lrange(trace_events_key(session_id), 0, -1)
        events: list[dict[str, Any]] = []
        for raw in raw_items or []:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            try:
                item = json.loads(raw)
            except (TypeError, json.JSONDecodeError):
                continue
            if isinstance(item, dict):
                events.append(item)
        events.sort(key=lambda row: str(row.get("started_at") or ""))
        return events

    async def set_meta(self, session_id: str, **fields: Any) -> None:
        self._assert_db3()
        key = trace_meta_key(session_id)
        meta = await self.get_meta(session_id)
        meta.update(fields)
        await self._redis.set(key, json.dumps(meta, ensure_ascii=False), ex=TRACE_TTL_SECONDS)

    async def get_meta(self, session_id: str) -> dict[str, Any]:
        self._assert_db3()
        raw = await self._redis.get(trace_meta_key(session_id))
        if raw is None:
            return {}
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

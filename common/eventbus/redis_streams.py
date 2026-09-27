"""Redis Streams EventBus adapter (spike no-go / optional EVENT_BUS value).

Go path (arm64 spike) uses ``rocketmq.py``; this module exists so factory can
return a correct adapter when ``EVENT_BUS=redis_streams``. Implements
consumer-group + XADD/XREADGROUP. Full PEL/XCLAIM/DLQ stream hardening is
for the no-go production path.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog
from redis.asyncio import Redis

from common.eventbus.base import PHYSICAL_TOPIC, EventHandler, consumer_group_name
from common.eventbus.envelope import (
    EventEnvelope,
    build_envelope,
    parse_envelope,
    peek_reject_meta,
)

logger = structlog.get_logger(__name__)

_DEFAULT_DB = 3
_DLQ_SUFFIX = ":dlq"


def _redis_from_env() -> Redis:
    return Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("QC_REDIS_DB", str(_DEFAULT_DB))),
        decode_responses=True,
    )


def _stream_key(event_name: str) -> str:
    return f"{PHYSICAL_TOPIC}:{event_name}"


class RedisStreamsEventBus:
    """EventBus over Redis Streams (consumer groups)."""

    def __init__(self, redis: Redis | None = None) -> None:
        self._redis = redis or _redis_from_env()
        self._owns_redis = redis is None
        self._tasks: list[asyncio.Task[None]] = []

    async def publish(
        self,
        event_name: str,
        data: dict[str, Any],
        *,
        key: str | None = None,
        trace_id: str | None = None,
        event_id: str | None = None,
    ) -> None:
        from common.eventbus.observability import emit_publish

        envelope = build_envelope(
            event_name, data, trace_id=trace_id, event_id=event_id
        )
        fields = {
            "payload": envelope.model_dump_json(),
            "key": key or "",
        }
        await self._redis.xadd(_stream_key(event_name), fields)
        await emit_publish(
            event_id=envelope.event_id,
            event_name=event_name,
            trace_id=envelope.trace_id,
        )
        logger.info(
            "eventbus_publish",
            transport="redis_streams",
            event_id=envelope.event_id,
            event_name=event_name,
            trace_id=envelope.trace_id,
            key=key,
        )

    async def subscribe(
        self, event_name: str, group: str, handler: EventHandler
    ) -> None:
        consumer_group = (
            group
            if group.startswith("gid_")
            else consumer_group_name(group, event_name)
        )
        stream = _stream_key(event_name)
        try:
            await self._redis.xgroup_create(stream, consumer_group, id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP means already exists — fine.
            if "BUSYGROUP" not in str(exc):
                raise
        task = asyncio.create_task(
            self._consume_loop(stream, event_name, consumer_group, handler),
            name=f"eventbus-streams-{event_name}-{consumer_group}",
        )
        self._tasks.append(task)
        logger.info(
            "eventbus_subscribe",
            transport="redis_streams",
            event_name=event_name,
            consumer_group=consumer_group,
        )

    async def _consume_loop(
        self,
        stream: str,
        event_name: str,
        consumer_group: str,
        handler: EventHandler,
    ) -> None:
        consumer_name = f"{consumer_group}-c0"
        while True:
            rows = await self._redis.xreadgroup(
                consumer_group,
                consumer_name,
                {stream: ">"},
                count=8,
                block=2000,
            )
            if not rows:
                continue
            for _s, messages in rows:
                for msg_id, fields in messages:
                    await self._handle_one(
                        stream, msg_id, fields, event_name, consumer_group, handler
                    )

    async def _handle_one(
        self,
        stream: str,
        msg_id: str,
        fields: dict[str, Any],
        event_name: str,
        consumer_group: str,
        handler: EventHandler,
    ) -> None:
        raw = fields.get("payload") or fields.get(b"payload")
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            envelope = parse_envelope(raw, fallback_event_name=event_name)
        except Exception as exc:
            eid, tid, rejected = peek_reject_meta(raw)
            logger.warning(
                "eventbus_consume_reject",
                transport="redis_streams",
                event_name=event_name,
                consumer_group=consumer_group,
                event_id=eid,
                trace_id=tid,
                rejected_field=rejected[0] if rejected else None,
                rejected_fields=rejected or None,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await self._redis.xack(stream, consumer_group, msg_id)
            return
        try:
            await handler(envelope)
            await self._redis.xack(stream, consumer_group, msg_id)
        except Exception as exc:
            dlq = stream + _DLQ_SUFFIX
            await self._redis.xadd(
                dlq,
                {
                    "payload": envelope.model_dump_json(),
                    "last_error": str(exc),
                    "consumer_group": consumer_group,
                },
            )
            await self._redis.xack(stream, consumer_group, msg_id)
            logger.error(
                "eventbus_streams_dlq",
                event_id=envelope.event_id,
                event_name=envelope.event_name,
                consumer_group=consumer_group,
                last_error=str(exc),
            )

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._owns_redis:
            await self._redis.aclose()

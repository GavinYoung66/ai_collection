"""Redis Pub/Sub EventBus adapter (historical reference only).

Not loaded by ``factory.get_event_bus`` (runtime accepts only ``rocketmq``).
Uses Redis logical DB 3. Process-local retry ×3 with exponential backoff +
structlog warning. No persistent DLQ.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import structlog
from redis.asyncio import Redis

from common.eventbus.base import EventHandler
from common.eventbus.envelope import (
    DATA_WHITELIST,
    EventEnvelope,
    build_envelope,
    parse_envelope,
    peek_reject_meta,
)
from common.eventbus.observability import emit_consume, emit_publish, emit_retry

logger = structlog.get_logger(__name__)

_DEFAULT_DB = 3
_MAX_HANDLER_ATTEMPTS = 3


def _redis_from_env() -> Redis:
    return Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        password=os.getenv("REDIS_PASSWORD") or None,
        db=int(os.getenv("QC_REDIS_DB", str(_DEFAULT_DB))),
        decode_responses=True,
    )


class RedisPubSubEventBus:
    """EventBus over Redis Pub/Sub (channel name = event_name)."""

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
        envelope = build_envelope(
            event_name, data, trace_id=trace_id, event_id=event_id
        )
        # Redis Pub/Sub has no message-key field; keep envelope JSON pure
        # (extra=forbid). ``key`` is log-only metadata on this transport.
        payload = envelope.model_dump(mode="json")
        await self._redis.publish(event_name, json.dumps(payload, ensure_ascii=False))
        await emit_publish(
            event_id=envelope.event_id,
            event_name=event_name,
            trace_id=envelope.trace_id,
        )
        logger.info(
            "eventbus_publish",
            transport="redis_pubsub",
            event_id=envelope.event_id,
            event_name=event_name,
            trace_id=envelope.trace_id,
            key=key,
        )

    async def subscribe(
        self, event_name: str, group: str, handler: EventHandler
    ) -> None:
        # Redis Pub/Sub has no consumer groups; ``group`` is kept for Protocol
        # parity and included in logs for ops correlation.
        task = asyncio.create_task(
            self._listen_loop(event_name, group, handler),
            name=f"eventbus-redis-{event_name}-{group}",
        )
        self._tasks.append(task)
        logger.info(
            "eventbus_subscribe",
            transport="redis_pubsub",
            event_name=event_name,
            group=group,
        )

    async def _listen_loop(
        self, event_name: str, group: str, handler: EventHandler
    ) -> None:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(event_name)
        try:
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                raw = message.get("data")
                try:
                    envelope = self._parse_envelope(raw, event_name)
                except Exception as exc:
                    eid, tid, rejected = peek_reject_meta(raw)
                    logger.warning(
                        "eventbus_consume_reject",
                        transport="redis_pubsub",
                        event_name=event_name,
                        group=group,
                        event_id=eid,
                        trace_id=tid,
                        rejected_field=rejected[0] if rejected else None,
                        rejected_fields=rejected or None,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    continue
                await self._dispatch_with_retry(envelope, group, handler)
        finally:
            await pubsub.unsubscribe(event_name)
            await pubsub.aclose()

    def _parse_envelope(self, raw: Any, event_name: str) -> EventEnvelope:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        body = json.loads(raw)
        if isinstance(body, dict) and "event_id" in body and "data" in body:
            return parse_envelope(body, fallback_event_name=event_name)
        # Legacy raw payload: only whitelist keys enter envelope data.
        legacy = body if isinstance(body, dict) else {}
        slim = {k: legacy[k] for k in DATA_WHITELIST if k in legacy}
        return build_envelope(event_name, slim)

    async def _dispatch_with_retry(
        self, envelope: EventEnvelope, group: str, handler: EventHandler
    ) -> None:
        last_error: Exception | None = None
        for attempt in range(1, _MAX_HANDLER_ATTEMPTS + 1):
            try:
                await handler(envelope)
                await emit_consume(
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    trace_id=envelope.trace_id,
                )
                return
            except Exception as exc:
                last_error = exc
                await emit_retry(
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    trace_id=envelope.trace_id,
                    attempt=attempt,
                    max_attempt=_MAX_HANDLER_ATTEMPTS,
                    error_type=type(exc).__name__,
                )
                logger.warning(
                    "eventbus_handler_retry",
                    transport="redis_pubsub",
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    group=group,
                    attempt=attempt,
                    max_attempt=_MAX_HANDLER_ATTEMPTS,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                if attempt < _MAX_HANDLER_ATTEMPTS:
                    await asyncio.sleep(2 ** (attempt - 1))
        logger.error(
            "eventbus_handler_exhausted",
            transport="redis_pubsub",
            event_id=envelope.event_id,
            event_name=envelope.event_name,
            group=group,
            last_error=str(last_error) if last_error else None,
            note="no_persistent_dlq",
        )

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._owns_redis:
            await self._redis.aclose()

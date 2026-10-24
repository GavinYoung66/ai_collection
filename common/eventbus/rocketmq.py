"""RocketMQ 5.x EventBus adapter (arm64 spike go path).

Design Decision 8: no Producer topics= pre-declare; FilterExpression for
subscribe; Proxy endpoint from ROCKETMQ_PROXY (container) or localhost:18081.
Task §5.1: app-level retry budget + produce to ``%DLQ%{consumer_group}`` +
``csmp.eventbus.dlq`` hook on final failure.
"""
from __future__ import annotations

import asyncio
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import structlog

from common.eventbus.base import (
    PHYSICAL_TOPIC,
    EventHandler,
    consumer_group_name,
    dlq_topic_name,
)
from common.eventbus.envelope import (
    EventEnvelope,
    build_envelope,
    parse_envelope,
    peek_reject_meta,
)
from common.eventbus.observability import (
    emit_consume,
    emit_dlq,
    emit_publish,
    emit_retry,
)

logger = structlog.get_logger(__name__)

# Spec: maxRetryTimes=3 → original + 3 retries = 4 deliveries.
MAX_RETRY_TIMES = 3
_DEFAULT_PROXY = "rocketmq-proxy:8081"
# SimpleConsumer.receive 的不可见时长（秒）：handler 在此窗口内 ACK，超时则消息
# 重新可见并被重投，ACK 会报 INVALID_RECEIPT_HANDLE。真 LLM 研判 handler 需 ~37s，
# 原 30s 必然超时；提到 300s（覆盖研判 + 慢 LLM + 重试，远小于投诉全局 30min）。
RECEIVE_INVISIBLE_DURATION = 300


def _proxy_endpoints() -> str:
    return os.getenv("ROCKETMQ_PROXY", _DEFAULT_PROXY)


class RocketMQEventBus:
    """EventBus over RocketMQ Proxy (gRPC); topic + tag routing."""

    def __init__(
        self,
        *,
        endpoints: str | None = None,
        topic: str = PHYSICAL_TOPIC,
        max_retry_times: int = MAX_RETRY_TIMES,
        parallelism: int = 1,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._endpoints = endpoints or _proxy_endpoints()
        self._topic = topic
        self._max_retry_times = max_retry_times
        # 消费并行度：1 = 同批逐条串行（与改造前行为一致）；>1 = 同批 gather 并发，
        # 由 Semaphore 保证全局在途 handler 数不超过 parallelism
        self._parallelism = max(1, parallelism)
        self._executor = executor or ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="eventbus-rmq"
        )
        self._owns_executor = executor is None
        # 发布专用线程池：消费循环里的 consumer.receive 是阻塞长轮询，单次可占住线程
        # 十余秒；若与发布共用同一个 4 线程池，订阅数一多就会把池占满，发布任务
        # 只能排队（实测 qc 4 个订阅 4 个 worker → 复核接口 publish 卡 12~19s）。
        self._publish_executor = ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="eventbus-rmq-pub"
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._producer = None

    @property
    def max_retry_times(self) -> int:
        return self._max_retry_times

    @property
    def parallelism(self) -> int:
        return self._parallelism

    def _ensure_producer(self):
        if self._producer is not None:
            return self._producer
        from rocketmq import ClientConfiguration, Credentials, Producer

        conf = ClientConfiguration(self._endpoints, Credentials(), request_timeout=5)
        # Decision 8: do NOT pass topics= at construction (startup hang on host Proxy).
        producer = Producer(conf)
        producer.startup()
        self._producer = producer
        return producer

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
        body = envelope.model_dump_json().encode("utf-8")
        loop = asyncio.get_running_loop()
        message_id = await loop.run_in_executor(
            self._publish_executor,
            self._publish_sync,
            event_name,
            body,
            key,
            self._topic,
        )
        await emit_publish(
            event_id=envelope.event_id,
            event_name=event_name,
            trace_id=envelope.trace_id,
        )
        logger.info(
            "eventbus_publish",
            transport="rocketmq",
            event_id=envelope.event_id,
            event_name=event_name,
            trace_id=envelope.trace_id,
            key=key,
            message_id=message_id,
            topic=self._topic,
        )

    def _publish_sync(
        self,
        event_name: str,
        body: bytes,
        key: str | None,
        topic: str,
    ) -> str:
        from rocketmq import Message

        producer = self._ensure_producer()
        msg = Message()
        msg.topic = topic
        msg.tag = event_name
        msg.body = body
        if key:
            msg.keys = key
        receipt = producer.send(msg)
        return getattr(receipt, "message_id", "") or ""

    async def subscribe(
        self, event_name: str, group: str, handler: EventHandler
    ) -> None:
        # ``group`` may be full gid_* or a svc short code (qc/dp/cs).
        consumer_group = (
            group
            if group.startswith("gid_")
            else consumer_group_name(group, event_name)
        )
        task = asyncio.create_task(
            self._consume_loop(event_name, consumer_group, handler),
            name=f"eventbus-rmq-{event_name}-{consumer_group}",
        )
        self._tasks.append(task)
        logger.info(
            "eventbus_subscribe",
            transport="rocketmq",
            event_name=event_name,
            consumer_group=consumer_group,
            max_retry_times=self._max_retry_times,
            topic=self._topic,
        )

    async def _consume_loop(
        self, event_name: str, consumer_group: str, handler: EventHandler
    ) -> None:
        loop = asyncio.get_running_loop()
        consumer = await loop.run_in_executor(
            self._executor, self._start_consumer, event_name, consumer_group
        )
        # 信号量限制同批在途 handler 数；parallelism=1 时逐条放行，等同改造前串行
        semaphore = asyncio.Semaphore(self._parallelism)
        try:
            while True:
                try:
                    messages = await loop.run_in_executor(
                        self._executor, consumer.receive, 8, RECEIVE_INVISIBLE_DURATION
                    )
                except Exception as exc:
                    logger.warning(
                        "eventbus_consumer_receive_failed",
                        transport="rocketmq",
                        event_name=event_name,
                        consumer_group=consumer_group,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    raise
                if not messages:
                    await asyncio.sleep(0.05)
                    continue
                # 同批消息 gather 分派；return_exceptions=True 保证单条失败不影响同批
                results = await asyncio.gather(
                    *(
                        self._handle_with_semaphore(
                            semaphore,
                            msg,
                            event_name,
                            consumer_group,
                            handler,
                            consumer,
                            loop,
                        )
                        for msg in messages
                    ),
                    return_exceptions=True,
                )
                # 异常自然上浮到此处：打 warn 后循环继续处理下一批；
                # 失败条未被 ack，由 Broker 不可见超时后重投
                for result in results:
                    if isinstance(result, Exception):
                        logger.warning(
                            "eventbus_consume_batch_error",
                            transport="rocketmq",
                            event_name=event_name,
                            consumer_group=consumer_group,
                            error=str(result),
                            error_type=type(result).__name__,
                        )
        except asyncio.CancelledError:
            raise
        finally:
            await loop.run_in_executor(self._executor, consumer.shutdown)

    async def _handle_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        msg: Any,
        event_name: str,
        consumer_group: str,
        handler: EventHandler,
        consumer: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        # 信号量内执行单条处理；parallelism=1 时退化为逐条串行 await
        async with semaphore:
            await self._handle_message(
                msg, event_name, consumer_group, handler, consumer, loop
            )

    def _start_consumer(self, event_name: str, consumer_group: str):
        from rocketmq import (
            ClientConfiguration,
            Credentials,
            FilterExpression,
            SimpleConsumer,
        )

        conf = ClientConfiguration(self._endpoints, Credentials(), request_timeout=5)
        consumer = SimpleConsumer(
            conf,
            consumer_group,
            subscription={self._topic: FilterExpression(event_name)},
        )
        consumer.startup()
        return consumer

    async def _handle_message(
        self,
        msg: Any,
        event_name: str,
        consumer_group: str,
        handler: EventHandler,
        consumer: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        raw = msg.body if isinstance(msg.body, (bytes, bytearray)) else bytes(msg.body)
        try:
            envelope = parse_envelope(raw, fallback_event_name=event_name)
        except Exception as exc:
            eid, tid, rejected = peek_reject_meta(raw)
            logger.warning(
                "eventbus_consume_reject",
                transport="rocketmq",
                event_name=event_name,
                consumer_group=consumer_group,
                event_id=eid,
                trace_id=tid,
                rejected_field=rejected[0] if rejected else None,
                rejected_fields=rejected or None,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            await loop.run_in_executor(self._executor, consumer.ack, msg)
            return

        attempt = 0
        last_error: Exception | None = None
        # Application-visible retry budget aligned with maxRetryTimes=3 (4 deliveries).
        while attempt <= self._max_retry_times:
            try:
                await handler(envelope)
                await emit_consume(
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    trace_id=envelope.trace_id,
                )
                await loop.run_in_executor(self._executor, consumer.ack, msg)
                return
            except Exception as exc:
                last_error = exc
                attempt += 1
                await emit_retry(
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    trace_id=envelope.trace_id,
                    attempt=attempt,
                    max_attempt=self._max_retry_times + 1,
                    error_type=type(exc).__name__,
                )
                logger.warning(
                    "eventbus_handler_retry",
                    transport="rocketmq",
                    event_id=envelope.event_id,
                    event_name=envelope.event_name,
                    consumer_group=consumer_group,
                    attempt=attempt,
                    max_attempt=self._max_retry_times + 1,
                    error=str(exc),
                    error_type=type(exc).__name__,
                )
                if attempt <= self._max_retry_times:
                    await asyncio.sleep(min(2 ** (attempt - 1), 8))

        dlq_ts = datetime.now(timezone.utc).isoformat()
        last_error_text = str(last_error) if last_error else "unknown"
        await self._send_to_dlq(envelope, consumer_group, last_error_text, loop)
        await emit_dlq(
            event_id=envelope.event_id,
            event_name=envelope.event_name,
            trace_id=envelope.trace_id,
            consumer_group=consumer_group,
            last_error=last_error_text,
        )
        logger.error(
            "eventbus_handler_exhausted",
            transport="rocketmq",
            event_id=envelope.event_id,
            event_name=envelope.event_name,
            consumer_group=consumer_group,
            trace_id=envelope.trace_id,
            last_error=last_error_text,
            dlq_timestamp=dlq_ts,
            dlq_topic=dlq_topic_name(consumer_group),
        )
        await loop.run_in_executor(self._executor, consumer.ack, msg)

    async def _send_to_dlq(
        self,
        envelope: EventEnvelope,
        consumer_group: str,
        last_error: str,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Produce failed message to ``%DLQ%{consumer_group}`` for replay."""
        body_obj = envelope.model_dump(mode="json")
        body_obj["_dlq"] = {
            "consumer_group": consumer_group,
            "last_error": last_error,
            "dlq_timestamp": datetime.now(timezone.utc).isoformat(),
            "original_topic": self._topic,
        }
        body = json.dumps(body_obj, ensure_ascii=False).encode("utf-8")
        topic = dlq_topic_name(consumer_group)
        try:
            await loop.run_in_executor(
                self._publish_executor,
                self._publish_sync,
                envelope.event_name,
                body,
                envelope.event_id,
                topic,
            )
        except Exception as exc:
            logger.error(
                "eventbus_dlq_produce_failed",
                event_id=envelope.event_id,
                event_name=envelope.event_name,
                consumer_group=consumer_group,
                dlq_topic=topic,
                error=str(exc),
                error_type=type(exc).__name__,
            )

    async def aclose(self) -> None:
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        if self._producer is not None:
            try:
                self._producer.shutdown()
            except Exception as exc:
                logger.warning("rocketmq_producer_shutdown_failed", error=str(exc))
            self._producer = None
        if self._owns_executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
        self._publish_executor.shutdown(wait=False, cancel_futures=True)

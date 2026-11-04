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
from dataclasses import dataclass, field
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
from common.resilience.retry import (
    EVENTBUS_RETRY,
    SUBSCRIBE_RETRY,
    retry_async,
    retry_delay,
)

logger = structlog.get_logger(__name__)

# Spec: maxRetryTimes=3 → original + 3 retries = 4 deliveries.
MAX_RETRY_TIMES = EVENTBUS_RETRY.max_attempts - 1
_DEFAULT_PROXY = "rocketmq-proxy:8081"
# SimpleConsumer.receive 的不可见时长（秒）：handler 在此窗口内 ACK，超时则消息
# 重新可见并被重投，ACK 会报 INVALID_RECEIPT_HANDLE。真 LLM 研判 handler 需 ~37s，
# 原 30s 必然超时；提到 300s（覆盖研判 + 慢 LLM + 重试，远小于投诉全局 30min）。
RECEIVE_INVISIBLE_DURATION = 300


def _parse_ts(value: datetime | str | None) -> datetime | None:
    """Parse an ISO8601 bound; naive values are treated as UTC."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _dlq_event_time(body: dict[str, Any]) -> datetime | None:
    meta = body.get("_dlq") if isinstance(body.get("_dlq"), dict) else {}
    raw = body.get("occurred_at") or meta.get("dlq_timestamp")
    if not raw:
        return None
    try:
        return _parse_ts(str(raw))
    except ValueError:
        return None


def _in_window(
    body: dict[str, Any], since: datetime | None, until: datetime | None
) -> bool:
    if since is None and until is None:
        return True
    ts = _dlq_event_time(body)
    if ts is None:
        return False
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


def _decode_dlq_body(raw: bytes | bytearray | str) -> dict[str, Any]:
    if isinstance(raw, (bytes, bytearray)):
        raw = bytes(raw).decode("utf-8")
    body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("DLQ body must be a JSON object")
    return body


def _receive_backoff_seconds(failures: int) -> float:
    """指数退避（1s 起，30s 封顶），供订阅循环在 receive 连续失败时使用。"""
    return retry_delay(failures, SUBSCRIBE_RETRY)


def _proxy_endpoints() -> str:
    return os.getenv("ROCKETMQ_PROXY", _DEFAULT_PROXY)


@dataclass
class _GroupSubscription:
    """一个服务共享消费组的注册表：tag → handler，一个消费循环服务全部事件。"""

    handlers: dict[str, EventHandler] = field(default_factory=dict)
    started: bool = False


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
        # 共享消费组：gid_{svc} → {tag: handler}，消费组数量不随事件数增长（T34）
        self._subscriptions: dict[str, _GroupSubscription] = {}
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
            group if group.startswith("gid_") else consumer_group_name(group)
        )
        sub = self._subscriptions.get(consumer_group)
        if sub is None:
            sub = _GroupSubscription()
            self._subscriptions[consumer_group] = sub
            task = asyncio.create_task(
                self._consume_group(consumer_group, sub.handlers),
                name=f"eventbus-rmq-{consumer_group}",
            )
            self._tasks.append(task)
        elif sub.started:
            # 订阅 tag 在 consumer 启动后不可修改：新增事件需重启进程生效（T34）
            logger.warning(
                "eventbus_subscribe_after_consumer_start",
                transport="rocketmq",
                event_name=event_name,
                consumer_group=consumer_group,
            )
        sub.handlers[event_name] = handler
        logger.info(
            "eventbus_subscribe",
            transport="rocketmq",
            event_name=event_name,
            consumer_group=consumer_group,
            subscribed_tags=sorted(sub.handlers),
            max_retry_times=self._max_retry_times,
            topic=self._topic,
        )

    async def _consume_group(
        self, consumer_group: str, handlers: dict[str, EventHandler]
    ) -> None:
        """单个共享消费组循环：一次订阅所有 tag，按 envelope tag 分派 handler。"""
        sub = self._subscriptions.get(consumer_group)
        if sub is not None:
            sub.started = True
        tags = tuple(sorted(handlers))
        loop = asyncio.get_running_loop()
        # Q-001 裁决（纳入 T12 边界）：consumer 启动失败与 receive 失败同属
        # 「订阅永久停止、无自动重启」缺陷类 —— 启动失败同样记日志 + 退避重试，
        # 不得让订阅任务静默终止。
        startup_failures = 0
        while True:
            try:
                consumer = await loop.run_in_executor(
                    self._executor, self._start_consumer, tags, consumer_group
                )
                break
            except Exception as exc:
                startup_failures += 1
                delay = _receive_backoff_seconds(startup_failures)
                logger.warning(
                    "eventbus_consumer_start_failed",
                    transport="rocketmq",
                    event_names=list(tags),
                    consumer_group=consumer_group,
                    error=str(exc),
                    error_type=type(exc).__name__,
                    consecutive_failures=startup_failures,
                    retry_in_seconds=delay,
                )
                await asyncio.sleep(delay)
        if startup_failures:
            logger.info(
                "eventbus_consumer_start_recovered",
                transport="rocketmq",
                event_names=list(tags),
                consumer_group=consumer_group,
                failures=startup_failures,
            )
        # 信号量限制同批在途 handler 数；parallelism=1 时逐条放行，等同改造前串行
        semaphore = asyncio.Semaphore(self._parallelism)
        receive_failures = 0
        try:
            while True:
                try:
                    messages = await loop.run_in_executor(
                        self._executor, consumer.receive, 8, RECEIVE_INVISIBLE_DURATION
                    )
                except Exception as exc:
                    # 订阅循环韧性：单次 receive 失败不得终止订阅（原实现 raise 后
                    # 任务永久退出、无人重启）。记录异常 + 指数退避后继续下一轮。
                    receive_failures += 1
                    delay = _receive_backoff_seconds(receive_failures)
                    logger.warning(
                        "eventbus_consumer_receive_failed",
                        transport="rocketmq",
                        event_names=list(tags),
                        consumer_group=consumer_group,
                        error=str(exc),
                        error_type=type(exc).__name__,
                        consecutive_failures=receive_failures,
                        retry_in_seconds=delay,
                    )
                    await asyncio.sleep(delay)
                    continue
                if receive_failures:
                    logger.info(
                        "eventbus_consumer_receive_recovered",
                        transport="rocketmq",
                        event_names=list(tags),
                        consumer_group=consumer_group,
                        failures=receive_failures,
                    )
                    receive_failures = 0
                if not messages:
                    await asyncio.sleep(0.05)
                    continue
                # 同批消息 gather 分派；return_exceptions=True 保证单条失败不影响同批
                results = await asyncio.gather(
                    *(
                        self._handle_with_semaphore(
                            semaphore,
                            msg,
                            consumer_group,
                            handlers,
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
                            event_names=list(tags),
                            consumer_group=consumer_group,
                            error=str(result),
                            error_type=type(result).__name__,
                        )
        except asyncio.CancelledError:
            raise
        finally:
            await loop.run_in_executor(self._executor, consumer.shutdown)

    def _resolve_handler(
        self, msg: Any, handlers: dict[str, EventHandler]
    ) -> tuple[str | None, EventHandler | None]:
        """按消息 tag 命中同组 handler；tag 缺失时仅在单订阅场景回退。"""
        tag = getattr(msg, "tag", None)
        if not isinstance(tag, str):
            tag = ""
        handler = handlers.get(tag)
        if handler is None and len(handlers) == 1:
            tag, handler = next(iter(handlers.items()))
            return tag, handler
        return (tag or None), handler

    async def _handle_with_semaphore(
        self,
        semaphore: asyncio.Semaphore,
        msg: Any,
        consumer_group: str,
        handlers: dict[str, EventHandler],
        consumer: Any,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        # 信号量内执行单条处理；parallelism=1 时退化为逐条串行 await
        async with semaphore:
            event_name, handler = self._resolve_handler(msg, handlers)
            if handler is None or event_name is None:
                logger.warning(
                    "eventbus_consume_unregistered_tag",
                    transport="rocketmq",
                    consumer_group=consumer_group,
                    message_tag=event_name,
                )
                await loop.run_in_executor(self._executor, consumer.ack, msg)
                return
            await self._handle_message(
                msg, event_name, consumer_group, handler, consumer, loop
            )

    def _start_consumer(self, tags: tuple[str, ...], consumer_group: str):
        from rocketmq import (
            ClientConfiguration,
            Credentials,
            FilterExpression,
            SimpleConsumer,
        )

        conf = ClientConfiguration(self._endpoints, Credentials(), request_timeout=5)
        # tag 过滤表达式：同一消费组一次订阅全部事件 tag（tag1 || tag2 ...）
        expression = " || ".join(tags) if tags else "*"
        consumer = SimpleConsumer(
            conf,
            consumer_group,
            subscription={self._topic: FilterExpression(expression)},
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

        last_error: Exception | None = None
        # Application-visible retry budget aligned with maxRetryTimes=3 (4 deliveries).
        policy = EVENTBUS_RETRY.with_overrides(
            max_attempts=self._max_retry_times + 1
        )

        async def _on_retry(attempt: int, exc: BaseException, delay: float) -> None:
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
                delay=delay,
                error=str(exc),
                error_type=type(exc).__name__,
            )

        try:
            await retry_async(
                lambda: handler(envelope), policy=policy, on_retry=_on_retry
            )
        except Exception as exc:
            last_error = exc
        else:
            await emit_consume(
                event_id=envelope.event_id,
                event_name=envelope.event_name,
                trace_id=envelope.trace_id,
            )
            await loop.run_in_executor(self._executor, consumer.ack, msg)
            return

        dlq_ts = datetime.now(timezone.utc).isoformat()
        last_error_text = str(last_error) if last_error else "unknown"
        dlq_produced = await self._send_to_dlq(
            envelope, consumer_group, last_error_text, loop
        )
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
            dlq_produced=dlq_produced,
        )
        if not dlq_produced:
            # 不丢语义：DLQ 生产失败时绝不 ACK，交由 Broker 不可见超时后重投，
            # 避免「确认即丢失」。下次重投仍会走完整重试 → 再次尝试入 DLQ。
            logger.error(
                "eventbus_dlq_produce_failed_no_ack",
                transport="rocketmq",
                event_id=envelope.event_id,
                event_name=envelope.event_name,
                consumer_group=consumer_group,
                trace_id=envelope.trace_id,
                dlq_topic=dlq_topic_name(consumer_group),
            )
            return
        await loop.run_in_executor(self._executor, consumer.ack, msg)

    async def _send_to_dlq(
        self,
        envelope: EventEnvelope,
        consumer_group: str,
        last_error: str,
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """Produce failed message to ``%DLQ%{consumer_group}`` for replay.

        Returns ``True`` only when the DLQ write succeeded. Callers MUST NOT ACK
        the original message when this returns ``False`` (at-least-once 语义)。
        """
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
            return True
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
            return False

    def _start_replay_consumer(self, topic: str, consumer_group: str):
        """Create a consumer bound to a ``%DLQ%`` topic for replay."""
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
            subscription={topic: FilterExpression("*")},
        )
        consumer.startup()
        return consumer

    async def replay_dlq(
        self,
        *,
        consumer_group: str,
        event_id: str | None = None,
        since: datetime | str | None = None,
        until: datetime | str | None = None,
        max_messages: int = 64,
        dry_run: bool = False,
    ) -> dict[str, int]:
        """Consume ``%DLQ%{consumer_group}`` and replay matching events.

        运维入口：按 ``event_id`` 精确命中，或按 ``since``/``until`` 时间窗口
        重放。命中的消息去掉 ``_dlq`` 元数据后重新投递到原 Topic；投递成功后
        ACK DLQ 消息，投递失败则不 ACK（留待下一次重放，保持不丢语义）。

        Returns:
            统计字典：``scanned`` / ``matched`` / ``replayed`` / ``skipped`` / ``failed``。
        """
        if not event_id and since is None and until is None:
            raise ValueError("replay_dlq requires event_id and/or since/until")
        since_dt = _parse_ts(since)
        until_dt = _parse_ts(until)
        if since_dt is not None and until_dt is not None and since_dt > until_dt:
            raise ValueError("replay_dlq since must be <= until")

        import uuid

        dlq_topic = dlq_topic_name(consumer_group)
        replay_group = (
            f"gid_replay_dry_{uuid.uuid4().hex[:10]}"
            if dry_run
            else f"gid_replay_{consumer_group}"
        )
        loop = asyncio.get_running_loop()
        consumer = await loop.run_in_executor(
            self._executor, self._start_replay_consumer, dlq_topic, replay_group
        )
        stats = {"scanned": 0, "matched": 0, "replayed": 0, "skipped": 0, "failed": 0}
        aborted = False
        try:
            while stats["scanned"] < max(1, max_messages) and not aborted:
                try:
                    messages = await loop.run_in_executor(
                        self._executor, consumer.receive, 8, 30
                    )
                except Exception as exc:
                    # DLQ Topic 尚未创建 / 读取失败：重放没有可用消息，正常退出。
                    logger.warning(
                        "eventbus_dlq_replay_receive_failed",
                        consumer_group=consumer_group,
                        dlq_topic=dlq_topic,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    break
                if not messages:
                    break
                for msg in messages:
                    if stats["scanned"] >= max(1, max_messages):
                        break
                    stats["scanned"] += 1
                    try:
                        body = _decode_dlq_body(msg.body)
                    except Exception as exc:
                        stats["skipped"] += 1
                        logger.warning(
                            "eventbus_dlq_replay_skip_unreadable",
                            consumer_group=consumer_group,
                            dlq_topic=dlq_topic,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        await loop.run_in_executor(self._executor, consumer.ack, msg)
                        continue

                    body_event_id = body.get("event_id")
                    if event_id and body_event_id != event_id:
                        stats["skipped"] += 1
                        await loop.run_in_executor(self._executor, consumer.ack, msg)
                        continue
                    if not _in_window(body, since_dt, until_dt):
                        stats["skipped"] += 1
                        await loop.run_in_executor(self._executor, consumer.ack, msg)
                        continue

                    stats["matched"] += 1
                    if dry_run:
                        logger.info(
                            "eventbus_dlq_replay_match",
                            consumer_group=consumer_group,
                            event_id=body_event_id,
                            dry_run=True,
                        )
                        await loop.run_in_executor(self._executor, consumer.ack, msg)
                        continue

                    dlq_meta = body.get("_dlq") if isinstance(body.get("_dlq"), dict) else {}
                    target_topic = dlq_meta.get("original_topic") or self._topic
                    body.pop("_dlq", None)
                    event_name = str(body.get("event_name") or "unknown")
                    try:
                        await loop.run_in_executor(
                            self._publish_executor,
                            self._publish_sync,
                            event_name,
                            json.dumps(body, ensure_ascii=False).encode("utf-8"),
                            body_event_id,
                            target_topic,
                        )
                    except Exception as exc:
                        stats["failed"] += 1
                        logger.error(
                            "eventbus_dlq_replay_failed",
                            consumer_group=consumer_group,
                            event_id=body_event_id,
                            event_name=event_name,
                            target_topic=target_topic,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        # 不 ACK：留给下一次重放，避免「重放失败即丢失」。
                        aborted = True
                        break
                    stats["replayed"] += 1
                    await loop.run_in_executor(self._executor, consumer.ack, msg)
                    logger.info(
                        "eventbus_dlq_replay",
                        consumer_group=consumer_group,
                        event_id=body_event_id,
                        event_name=event_name,
                        target_topic=target_topic,
                    )
        finally:
            await loop.run_in_executor(self._executor, consumer.shutdown)

        logger.info(
            "eventbus_dlq_replay_summary",
            consumer_group=consumer_group,
            dlq_topic=dlq_topic,
            dry_run=dry_run,
            **stats,
        )
        return stats

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

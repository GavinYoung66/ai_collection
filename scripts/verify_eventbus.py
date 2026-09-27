#!/usr/bin/env python3
"""EventBus smoke: publish ``verify.test`` and consume once.

Usage:
  python scripts/verify_eventbus.py --mode redis
  python scripts/verify_eventbus.py --mode rocketmq

Prints ``PASS`` on success. Safe to run twice (unique event_id each run).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


async def _run(mode: str) -> None:
    from common.eventbus.envelope import EventEnvelope
    from common.eventbus.factory import get_event_bus, reset_event_bus
    from common.eventbus.redis_pubsub import RedisPubSubEventBus

    os.environ["EVENT_BUS"] = mode
    os.environ["EVENT_BUS_SVC"] = "csmp"
    if mode == "rocketmq" and not os.getenv("ROCKETMQ_PROXY"):
        os.environ["ROCKETMQ_PROXY"] = "localhost:18081"
    if mode == "redis":
        os.environ.setdefault("REDIS_HOST", "localhost")
        os.environ.setdefault("REDIS_PORT", "6379")
        os.environ.setdefault("QC_REDIS_DB", "3")

    reset_event_bus()
    if mode == "redis":
        bus = RedisPubSubEventBus()
    else:
        bus = get_event_bus(force_new=True)

    event_name = "verify.test"
    event_id = f"evt_verify_{uuid.uuid4().hex[:12]}"
    session_id = f"verify-{uuid.uuid4().hex[:8]}"
    received: list[EventEnvelope] = []
    done = asyncio.Event()

    async def handler(envelope: EventEnvelope) -> None:
        if envelope.event_id == event_id:
            received.append(envelope)
            done.set()

    # RocketMQ: warm publish so Proxy has topic route before consume loop.
    # Retries cover cold Proxy route cache after mqadmin createTopic.
    if mode == "rocketmq":
        last_exc: Exception | None = None
        for attempt in range(1, 6):
            try:
                await bus.publish(
                    event_name,
                    {"session_id": f"warmup-{session_id}"},
                    key=f"warmup-{session_id}",
                    event_id=f"evt_warmup_{uuid.uuid4().hex[:8]}",
                    trace_id=f"tr_warmup_{uuid.uuid4().hex[:8]}",
                )
                last_exc = None
                break
            except Exception as exc:  # noqa: BLE001 — smoke retry
                last_exc = exc
                await asyncio.sleep(0.5 * attempt)
        if last_exc is not None:
            raise last_exc
        await asyncio.sleep(1.0)

    await bus.subscribe(event_name, "csmp", handler)
    await asyncio.sleep(0.3 if mode == "redis" else 1.5)
    await bus.publish(
        event_name,
        {"session_id": session_id},
        key=session_id,
        event_id=event_id,
        trace_id=f"tr_verify_{uuid.uuid4().hex[:8]}",
    )
    try:
        await asyncio.wait_for(done.wait(), timeout=20.0 if mode == "rocketmq" else 5.0)
    except TimeoutError as exc:
        raise RuntimeError(f"timeout waiting for {event_name} event_id={event_id}") from exc
    finally:
        close = getattr(bus, "aclose", None)
        if close is not None:
            await close()
        reset_event_bus()

    assert len(received) == 1, received
    assert received[0].data.get("session_id") == session_id
    print(f"PASS mode={mode} event_id={event_id} session_id={session_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify EventBus publish/subscribe")
    parser.add_argument(
        "--mode",
        choices=("redis", "rocketmq"),
        required=True,
        help="Transport mode to verify",
    )
    args = parser.parse_args(argv)
    try:
        asyncio.run(_run(args.mode))
    except Exception as exc:
        print(f"FAIL mode={args.mode} error={exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

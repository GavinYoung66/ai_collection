#!/usr/bin/env python3
"""Replay messages from RocketMQ DLQ Topic back to ``csmp_business_events``.

Usage:
  python scripts/event_replay.py --event-id evt_xxx --dry-run
  python scripts/event_replay.py --event-id evt_xxx
  python scripts/event_replay.py --since 2026-09-27T00:00:00Z --until 2026-09-27T23:59:59Z

DLQ topic naming: ``%DLQ%{consumer_group}``. Default group can be overridden
with ``--consumer-group``.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Repo root on PYTHONPATH
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.eventbus.base import PHYSICAL_TOPIC, dlq_topic_name  # noqa: E402


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _envelope_from_body(raw: bytes | str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(raw, dict):
        body = raw
    else:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        body = json.loads(raw)
    if not isinstance(body, dict):
        raise ValueError("DLQ body must be a JSON object")
    return body


def _in_window(
    body: dict[str, Any],
    *,
    since: datetime | None,
    until: datetime | None,
) -> bool:
    if since is None and until is None:
        return True
    occurred = body.get("occurred_at")
    dlq_meta = body.get("_dlq") if isinstance(body.get("_dlq"), dict) else {}
    ts_raw = occurred or dlq_meta.get("dlq_timestamp")
    if not ts_raw:
        return False
    if isinstance(ts_raw, str) and ts_raw.endswith("Z"):
        ts_raw = ts_raw[:-1] + "+00:00"
    ts = datetime.fromisoformat(str(ts_raw))
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    if since is not None and ts < since:
        return False
    if until is not None and ts > until:
        return False
    return True


def _strip_dlq_meta(body: dict[str, Any]) -> dict[str, Any]:
    out = dict(body)
    out.pop("_dlq", None)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay EventBus DLQ messages")
    parser.add_argument("--event-id", help="Replay a single event_id")
    parser.add_argument("--since", help="ISO8601 lower bound (inclusive)")
    parser.add_argument("--until", help="ISO8601 upper bound (inclusive)")
    parser.add_argument(
        "--consumer-group",
        default=os.getenv("EVENT_REPLAY_GROUP", "gid_qc_etl_task_done"),
        help="Consumer group whose %%DLQ%% topic to read",
    )
    parser.add_argument(
        "--proxy",
        default=os.getenv("ROCKETMQ_PROXY", "localhost:18081"),
        help="RocketMQ Proxy endpoint",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matched messages only; do not re-produce",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=64,
        help="Max receive batch attempts from DLQ",
    )
    args = parser.parse_args(argv)

    if not args.event_id and not args.since and not args.until:
        parser.error("provide --event-id and/or --since/--until")

    since = _parse_ts(args.since)
    until = _parse_ts(args.until)
    dlq_topic = dlq_topic_name(args.consumer_group)
    target_topic = PHYSICAL_TOPIC

    from rocketmq import (
        ClientConfiguration,
        Credentials,
        FilterExpression,
        Message,
        Producer,
        SimpleConsumer,
    )

    conf = ClientConfiguration(args.proxy, Credentials(), request_timeout=5)
    # Dry-run uses an ephemeral group so inspecting does not steal offsets from
    # the stable replay group (RocketMQ offsets are per consumer group).
    if args.dry_run:
        import uuid as _uuid

        consumer_group = f"gid_replay_dry_{_uuid.uuid4().hex[:10]}"
    else:
        consumer_group = f"gid_replay_{args.consumer_group}"
    consumer = SimpleConsumer(
        conf,
        consumer_group,
        subscription={dlq_topic: FilterExpression("*")},
    )
    consumer.startup()
    producer = None if args.dry_run else Producer(conf)
    if producer is not None:
        producer.startup()

    matched: list[dict[str, Any]] = []
    try:
        try:
            for _ in range(max(1, args.max_messages)):
                try:
                    # invisible_duration unit is seconds; 30s is enough for replay.
                    messages = consumer.receive(8, 30)
                except Exception as exc:
                    # Empty / never-created DLQ topic is a valid dry-run outcome.
                    print(
                        f"DLQ_READ_NOTE error={exc} dlq_topic={dlq_topic}",
                        file=sys.stderr,
                    )
                    break
                if not messages:
                    break
                for msg in messages:
                    try:
                        body = _envelope_from_body(msg.body)
                    except Exception as exc:
                        print(f"SKIP unreadable DLQ body: {exc}", file=sys.stderr)
                        consumer.ack(msg)
                        continue
                    event_id = body.get("event_id")
                    if args.event_id and event_id != args.event_id:
                        consumer.ack(msg)
                        continue
                    if not _in_window(body, since=since, until=until):
                        consumer.ack(msg)
                        continue
                    matched.append(body)
                    clean = _strip_dlq_meta(body)
                    event_name = str(clean.get("event_name") or "unknown")
                    print(
                        f"MATCH event_id={event_id} event_name={event_name} "
                        f"dlq_topic={dlq_topic} -> topic={target_topic}"
                    )
                    if not args.dry_run and producer is not None:
                        out = Message()
                        out.topic = target_topic
                        out.tag = event_name
                        out.body = json.dumps(clean, ensure_ascii=False).encode("utf-8")
                        if event_id:
                            out.keys = str(event_id)
                        receipt = producer.send(out)
                        mid = getattr(receipt, "message_id", "") or ""
                        print(f"REPLAYED event_id={event_id} message_id={mid}")
                    # Ack in dry-run too (ephemeral group); real replay group offsets stay intact.
                    consumer.ack(msg)
        except Exception as exc:
            print(f"DLQ_SCAN_FAILED error={exc}", file=sys.stderr)
            return 1
    finally:
        consumer.shutdown()
        if producer is not None:
            producer.shutdown()

    print(
        f"SUMMARY matched={len(matched)} dry_run={args.dry_run} "
        f"dlq_topic={dlq_topic} target_topic={target_topic}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

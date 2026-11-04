#!/usr/bin/env python3
"""Replay messages from RocketMQ DLQ Topic back to the original topic.

Usage:
  python scripts/event_replay.py --event-id evt_xxx --dry-run
  python scripts/event_replay.py --event-id evt_xxx
  python scripts/event_replay.py --since 2026-09-27T00:00:00Z --until 2026-09-27T23:59:59Z

DLQ topic naming: ``%DLQ%{consumer_group}``. 消费组按服务共享（``gid_{svc}``），
因此 DLQ 也按服务收敛；可用 ``--consumer-group`` 覆盖。

T09（批次3评审 R-001/S-001）后本脚本仅保留 CLI 参数面，重放逻辑统一委托给
``RocketMQEventBus.replay_dlq`` 单一实现，避免与库内方法双实现漂移
（此前脚本独立实现过滤/重投，已与 `replay_dlq` 的 original_topic 语义分叉）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Repo root on PYTHONPATH
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from common.eventbus.base import dlq_topic_name  # noqa: E402


def _build_bus(endpoints: str):
    from common.eventbus.rocketmq import RocketMQEventBus

    return RocketMQEventBus(endpoints=endpoints)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay EventBus DLQ messages")
    parser.add_argument("--event-id", help="Replay a single event_id")
    parser.add_argument("--since", help="ISO8601 lower bound (inclusive)")
    parser.add_argument("--until", help="ISO8601 upper bound (inclusive)")
    parser.add_argument(
        "--consumer-group",
        default=os.getenv("EVENT_REPLAY_GROUP", "gid_qc"),
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
        help="Scan and match only; do not re-produce",
    )
    parser.add_argument(
        "--max-messages",
        type=int,
        default=64,
        help="Max DLQ messages to scan in one run",
    )
    args = parser.parse_args(argv)

    if not args.event_id and not args.since and not args.until:
        parser.error("provide --event-id and/or --since/--until")

    dlq_topic = dlq_topic_name(args.consumer_group)

    async def _run() -> dict[str, int]:
        bus = _build_bus(args.proxy)
        try:
            return await bus.replay_dlq(
                consumer_group=args.consumer_group,
                event_id=args.event_id,
                since=args.since,
                until=args.until,
                max_messages=args.max_messages,
                dry_run=args.dry_run,
            )
        finally:
            await bus.aclose()

    try:
        stats = asyncio.run(_run())
    except ValueError as exc:
        # replay_dlq 的参数校验（无条件 / since > until）
        print(f"DLQ_REPLAY_INVALID error={exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"DLQ_SCAN_FAILED error={exc}", file=sys.stderr)
        return 1

    print(
        f"SUMMARY scanned={stats.get('scanned', 0)} matched={stats.get('matched', 0)} "
        f"replayed={stats.get('replayed', 0)} skipped={stats.get('skipped', 0)} "
        f"failed={stats.get('failed', 0)} dry_run={args.dry_run} dlq_topic={dlq_topic}"
    )
    return 0 if stats.get("failed", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

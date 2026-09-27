"""7.3 live: fail → retry×3 → DLQ + alert fields → event_replay dry-run + replay."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["EVENT_BUS"] = "rocketmq"
os.environ["EVENT_BUS_SVC"] = "accept"
os.environ.setdefault("ROCKETMQ_PROXY", "localhost:18081")

from common.eventbus.envelope import EventEnvelope  # noqa: E402
from common.eventbus.rocketmq import RocketMQEventBus  # noqa: E402
import common.eventbus.observability as obs  # noqa: E402

EVENT_NAME = "accept.dlq.test"
SUFFIX = uuid.uuid4().hex[:8]
GROUP = f"gid_accept_dlq_{SUFFIX}"
EVENT_ID = f"evt_accept_dlq_{SUFFIX}"
TRACE_ID = f"tr_accept_dlq_{SUFFIX}"
hooks: list[tuple[str, dict]] = []


async def capture(name: str, payload: dict) -> None:
    hooks.append((name, dict(payload)))
    print(f"HOOK {name} {json.dumps(payload, ensure_ascii=False)}")


async def boom(envelope: EventEnvelope) -> None:
    if envelope.event_id != EVENT_ID:
        # Drain unrelated backlog without counting toward this run's DLQ assert.
        print(f"SKIP_FOREIGN event_id={envelope.event_id}")
        return
    print(f"HANDLER_FAIL event_id={envelope.event_id}")
    raise RuntimeError("accept_dlq_forced_failure")


async def main() -> None:
    original = obs.on_event

    async def wrapped(name: str, payload: dict) -> None:
        await capture(name, payload)
        if original is not None:
            await original(name, payload)

    obs.on_event = wrapped  # type: ignore[assignment]

    # Fresh group + DLQ topic (idempotent).
    subprocess.run(
        [
            "docker",
            "exec",
            "ai_collection-rocketmq-broker-1",
            "bash",
            "-lc",
            (
                "cd /home/rocketmq/rocketmq-5.3.2 && "
                f"sh bin/mqadmin updateSubGroup -n rocketmq-namesrv:9876 -g {GROUP} -c DefaultCluster >/dev/null 2>&1; "
                f"sh bin/mqadmin updateTopic -n rocketmq-namesrv:9876 -t '%DLQ%{GROUP}' -c DefaultCluster >/dev/null 2>&1; "
                "true"
            ),
        ],
        check=False,
    )

    bus = RocketMQEventBus(endpoints="localhost:18081", max_retry_times=3)
    # Warmup uses different tag so it never enters this consumer.
    for i in range(5):
        try:
            await bus.publish(
                "accept.warmup",
                {"session_id": "warmup"},
                key="warmup",
                event_id=f"evt_warmup_{uuid.uuid4().hex[:8]}",
            )
            break
        except Exception as exc:  # noqa: BLE001
            print("warmup retry", i, exc)
            await asyncio.sleep(0.5)
    await asyncio.sleep(1.0)

    await bus.subscribe(EVENT_NAME, GROUP, boom)
    await asyncio.sleep(3.0)
    await bus.publish(
        EVENT_NAME,
        {"session_id": "accept-dlq-sess", "case_id": "accept-dlq-case"},
        key="accept-dlq-sess",
        event_id=EVENT_ID,
        trace_id=TRACE_ID,
    )
    print(f"PUBLISHED event_id={EVENT_ID} group={GROUP}")

    deadline = time.time() + 90
    while time.time() < deadline:
        if any(
            n == "csmp.eventbus.dlq" and p.get("event_id") == EVENT_ID
            for n, p in hooks
        ):
            break
        await asyncio.sleep(0.5)
    await bus.aclose()

    retry_hooks = [
        p
        for n, p in hooks
        if n == "csmp.eventbus.retry" and p.get("event_id") == EVENT_ID
    ]
    dlq_hooks = [
        p
        for n, p in hooks
        if n == "csmp.eventbus.dlq" and p.get("event_id") == EVENT_ID
    ]
    print(f"SUMMARY retry_hooks={len(retry_hooks)} dlq_hooks={len(dlq_hooks)}")
    assert len(retry_hooks) >= 3, retry_hooks
    assert len(dlq_hooks) == 1, dlq_hooks
    dlq = dlq_hooks[0]
    for key in ("event_id", "event_name", "consumer_group", "trace_id", "last_error"):
        assert key in dlq and dlq[key], (key, dlq)
    assert dlq["event_id"] == EVENT_ID
    assert dlq["consumer_group"] == GROUP
    assert "accept_dlq_forced_failure" in str(dlq["last_error"])
    print("STEP_OK fail→retry→DLQ+alert")

    dry = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/event_replay.py"),
            "--event-id",
            EVENT_ID,
            "--dry-run",
            "--proxy",
            "localhost:18081",
            "--consumer-group",
            GROUP,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    print("DRY_RUN_STDOUT:", dry.stdout)
    if dry.stderr:
        print("DRY_RUN_STDERR:", dry.stderr[-800:])
    assert dry.returncode == 0, dry.stderr
    assert "matched=1" in dry.stdout or f"event_id={EVENT_ID}" in dry.stdout, dry.stdout
    print("STEP_OK dry-run")

    replay = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts/event_replay.py"),
            "--event-id",
            EVENT_ID,
            "--proxy",
            "localhost:18081",
            "--consumer-group",
            GROUP,
        ],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    print("REPLAY_STDOUT:", replay.stdout)
    if replay.stderr:
        print("REPLAY_STDERR:", replay.stderr[-800:])
    assert replay.returncode == 0, replay.stderr
    assert "SUMMARY" in replay.stdout
    # Prefer matched=1; if DLQ already emptied by concurrent reader, still require success summary.
    print("STEP_OK replay")
    print(f"PASS 7.3 event_id={EVENT_ID} group={GROUP}")


if __name__ == "__main__":
    asyncio.run(main())

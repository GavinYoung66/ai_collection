"""T09：DLQ 重放入口（按 event_id / 时间窗口命中，重放失败不 ACK）。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from common.eventbus.base import PHYSICAL_TOPIC
from common.eventbus.envelope import build_envelope
from common.eventbus.rocketmq import RocketMQEventBus


class _ScriptedConsumer:
    """脚本化 DLQ consumer：按批返回，随后返回空批。"""

    def __init__(self, batches: list[list[Any]]) -> None:
        self._batches = list(batches)
        self.acked: list[Any] = []
        self.shutdown_called = False

    def receive(self, *_a: Any, **_k: Any) -> list[Any]:
        return self._batches.pop(0) if self._batches else []

    def ack(self, msg: Any, *_a: Any, **_k: Any) -> None:
        self.acked.append(msg)

    def shutdown(self) -> None:
        self.shutdown_called = True


def _dlq_msg(
    event_id: str,
    *,
    occurred_at: str = "2026-11-02T10:00:00+00:00",
    original_topic: str = PHYSICAL_TOPIC,
) -> MagicMock:
    envelope = build_envelope(
        "complaint.created", {"case_id": event_id}, event_id=event_id
    ).model_dump(mode="json")
    envelope["occurred_at"] = occurred_at
    envelope["_dlq"] = {
        "consumer_group": "gid_cs_complaint_created",
        "last_error": "boom",
        "dlq_timestamp": occurred_at,
        "original_topic": original_topic,
    }
    msg = MagicMock()
    msg.body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return msg


@pytest.mark.asyncio
async def test_replay_by_event_id_hits_exact_message():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    match = _dlq_msg("evt_target")
    other = _dlq_msg("evt_other")
    consumer = _ScriptedConsumer([[other, match]])
    published: list[tuple] = []

    def fake_publish(event_name, body, key, topic):
        published.append((event_name, body, key, topic))
        return "mid-replay"

    with (
        patch.object(bus, "_start_replay_consumer", return_value=consumer),
        patch.object(bus, "_publish_sync", side_effect=fake_publish),
    ):
        stats = await bus.replay_dlq(
            consumer_group="gid_cs_complaint_created", event_id="evt_target"
        )
    await bus.aclose()

    assert stats == {
        "scanned": 2,
        "matched": 1,
        "replayed": 1,
        "skipped": 1,
        "failed": 0,
    }
    assert consumer.acked == [other, match]
    assert len(published) == 1
    _, body, key, topic = published[0]
    assert key == "evt_target"
    assert topic == PHYSICAL_TOPIC
    assert "_dlq" not in json.loads(body)


@pytest.mark.asyncio
async def test_replay_by_time_window_filters_outside_messages():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    inside = _dlq_msg("evt_inside", occurred_at="2026-11-02T10:05:00+00:00")
    outside = _dlq_msg("evt_outside", occurred_at="2026-11-01T10:00:00+00:00")
    consumer = _ScriptedConsumer([[outside, inside]])
    published: list[Any] = []

    def fake_publish(event_name, body, key, topic):
        published.append(key)
        return "mid"

    with (
        patch.object(bus, "_start_replay_consumer", return_value=consumer),
        patch.object(bus, "_publish_sync", side_effect=fake_publish),
    ):
        stats = await bus.replay_dlq(
            consumer_group="gid_cs_complaint_created",
            since="2026-11-02T00:00:00Z",
            until="2026-11-02T23:59:59Z",
        )
    await bus.aclose()

    assert stats["matched"] == 1
    assert stats["replayed"] == 1
    assert stats["skipped"] == 1
    assert published == ["evt_inside"]


@pytest.mark.asyncio
async def test_replay_dry_run_does_not_publish_but_acks():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    match = _dlq_msg("evt_dry")
    consumer = _ScriptedConsumer([[match]])

    with (
        patch.object(bus, "_start_replay_consumer", return_value=consumer),
        patch.object(bus, "_publish_sync") as publish,
    ):
        stats = await bus.replay_dlq(
            consumer_group="gid_cs_complaint_created",
            event_id="evt_dry",
            dry_run=True,
        )
    await bus.aclose()

    assert stats["matched"] == 1
    assert stats["replayed"] == 0
    assert consumer.acked == [match]
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_replay_publish_failure_does_not_ack():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    other = _dlq_msg("evt_skip")
    match = _dlq_msg("evt_fail")
    consumer = _ScriptedConsumer([[other, match]])

    def boom_publish(*_args):
        raise RuntimeError("topic unavailable")

    with (
        patch.object(bus, "_start_replay_consumer", return_value=consumer),
        patch.object(bus, "_publish_sync", side_effect=boom_publish),
    ):
        stats = await bus.replay_dlq(
            consumer_group="gid_cs_complaint_created", event_id="evt_fail"
        )
    await bus.aclose()

    assert stats["matched"] == 1
    assert stats["replayed"] == 0
    assert stats["failed"] == 1
    # 命中消息重放失败 -> 不 ACK，留给下一次重放
    assert consumer.acked == [other]


@pytest.mark.asyncio
async def test_replay_receive_error_soft_exits_and_shuts_down():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    consumer = _ScriptedConsumer([])

    def raise_receive(*_args):
        raise RuntimeError("dlq topic not created")

    consumer.receive = raise_receive

    with patch.object(bus, "_start_replay_consumer", return_value=consumer):
        stats = await bus.replay_dlq(
            consumer_group="gid_cs_complaint_created", event_id="evt_missing"
        )
    await bus.aclose()

    assert stats == {
        "scanned": 0,
        "matched": 0,
        "replayed": 0,
        "skipped": 0,
        "failed": 0,
    }
    assert consumer.shutdown_called is True


@pytest.mark.asyncio
async def test_replay_requires_criteria_and_valid_window():
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    with pytest.raises(ValueError, match="event_id and/or since/until"):
        await bus.replay_dlq(consumer_group="gid_cs_complaint_created")
    with pytest.raises(ValueError, match="since must be <= until"):
        await bus.replay_dlq(
            consumer_group="gid_cs_complaint_created",
            since=datetime(2026, 11, 3, tzinfo=timezone.utc),
            until=datetime(2026, 11, 2, tzinfo=timezone.utc),
        )
    await bus.aclose()


# ---------------------------------------------------------------------------
# 运维 CLI（scripts/event_replay.py）：委托 replay_dlq 单一实现（R-001/S-001）
# ---------------------------------------------------------------------------
import importlib.util  # noqa: E402
from pathlib import Path  # noqa: E402

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "event_replay.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("event_replay_cli", str(_SCRIPT))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_cli_delegates_args_to_replay_dlq(monkeypatch, capsys):
    """CLI 保留参数面，重放逻辑必须委托 bus.replay_dlq（不再自带实现）。"""
    mod = _load_cli()
    captured: dict = {}

    class _FakeBus:
        def __init__(self, endpoints: str) -> None:
            captured["endpoints"] = endpoints

        async def replay_dlq(self, **kwargs):
            captured.update(kwargs)
            return {
                "scanned": 2,
                "matched": 1,
                "replayed": 1,
                "skipped": 1,
                "failed": 0,
            }

        async def aclose(self) -> None:
            captured["closed"] = True

    monkeypatch.setattr(mod, "_build_bus", lambda endpoints: _FakeBus(endpoints))

    rc = mod.main(
        [
            "--event-id",
            "evt_x",
            "--consumer-group",
            "gid_a",
            "--proxy",
            "p:8081",
            "--max-messages",
            "7",
        ]
    )

    assert rc == 0
    assert captured["endpoints"] == "p:8081"
    assert captured["event_id"] == "evt_x"
    assert captured["consumer_group"] == "gid_a"
    assert captured["max_messages"] == 7
    assert captured["dry_run"] is False
    assert captured["closed"] is True
    out = capsys.readouterr().out
    assert "SUMMARY" in out and "replayed=1" in out and "failed=0" in out


def test_cli_dry_run_and_failed_exit_codes(monkeypatch, capsys):
    """dry_run 透传；replay 失败（failed>0）返回 1；非法窗口返回 2。"""
    mod = _load_cli()
    captured: dict = {}

    class _FakeBus:
        def __init__(self, endpoints: str) -> None:  # noqa: ARG002
            pass

        async def replay_dlq(self, **kwargs):
            captured.update(kwargs)
            if captured.get("since") == "2026-11-03T00:00:00Z":
                raise ValueError("replay_dlq since must be <= until")
            return {
                "scanned": 1,
                "matched": 1,
                "replayed": 0,
                "skipped": 0,
                "failed": 1,
            }

        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(mod, "_build_bus", lambda endpoints: _FakeBus(endpoints))

    rc_dry = mod.main(["--event-id", "e1", "--dry-run"])
    assert rc_dry == 1  # failed=1 → 1
    assert captured["dry_run"] is True

    rc_bad = mod.main(
        ["--event-id", "e1", "--since", "2026-11-03T00:00:00Z", "--until", "2026-11-01T00:00:00Z"]
    )
    assert rc_bad == 2

    with pytest.raises(SystemExit):
        mod.main([])  # 无任何筛选条件 → argparse error

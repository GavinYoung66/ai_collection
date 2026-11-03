"""EventBus observability hooks（覆盖对象：生产运行模式 RocketMQEventBus）。

T31 删除 Redis 双适配器死代码后，publish/consume/retry/dlq 埋点钩子的
等价覆盖改由 RocketMQ 总线承载（直调 _handle_message / _send_to_dlq 打桩）。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from common.eventbus.envelope import build_envelope
from common.eventbus.rocketmq import RocketMQEventBus


def _msg(body: bytes) -> SimpleNamespace:
    return SimpleNamespace(body=body)


def _rocketmq_asyncio_shim(monkeypatch) -> None:
    """将 retry 退避 asyncio.sleep 换成空转，避免测试真实等待 1+2+4 秒。"""

    async def _nosleep(_seconds: float) -> None:
        return None

    import common.eventbus.rocketmq as rocketmq_module
    import common.resilience.retry as retry_module

    monkeypatch.setattr(
        rocketmq_module,
        "asyncio",
        SimpleNamespace(sleep=_nosleep, run_in_executor=None),
    )
    # T28：退避 sleep 由统一原语执行，patch 点同步收敛。
    monkeypatch.setattr(retry_module, "asyncio", SimpleNamespace(sleep=_nosleep))


@pytest.mark.asyncio
async def test_publish_and_consume_emit_hooks(monkeypatch):
    """正常：publish 与 consume 终态各发一条钩子，payload 含 event_id/name/trace_id/svc。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    hooks: list[tuple[str, dict[str, Any]]] = []

    async def capture(name: str, payload: dict[str, Any]) -> None:
        hooks.append((name, payload))

    class _Receipt:
        message_id = "mid-1"

    class _FakeProducer:
        def startup(self):
            return None

        def send(self, message):
            return _Receipt()

        def shutdown(self):
            return None

    class _FakeConsumer:
        def ack(self, *_a, **_k):
            return None

    with (
        patch("common.eventbus.observability.on_event", new=AsyncMock(side_effect=capture)),
        patch.object(bus, "_ensure_producer", return_value=_FakeProducer()),
    ):
        await bus.publish(
            "etl.task.done",
            {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
            key="s1",
            trace_id="tr_hook_1",
            event_id="evt_hook_1",
        )
        envelope = build_envelope(
            "etl.task.done",
            {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
            trace_id="tr_hook_1",
            event_id="evt_hook_1",
        )
        await bus._handle_message(
            _msg(envelope.model_dump_json().encode("utf-8")),
            "etl.task.done",
            "gid_qc_etl_task_done",
            AsyncMock(),  # handler 成功
            _FakeConsumer(),
            asyncio.get_running_loop(),
        )

    await bus.aclose()
    names = [n for n, _ in hooks]
    assert "csmp.eventbus.publish" in names
    assert "csmp.eventbus.consume" in names
    pub = next(p for n, p in hooks if n == "csmp.eventbus.publish")
    assert pub["event_id"] == "evt_hook_1"
    assert pub["event_name"] == "etl.task.done"
    assert pub["trace_id"] == "tr_hook_1"
    assert "svc" in pub


@pytest.mark.asyncio
async def test_retry_exhaustion_emits_retry_hooks_then_dlq_hook(monkeypatch):
    """异常：handler 持续失败 → retry 钩子 ×3（对齐 maxRetryTimes）→ DLQ 钩子恰好一次。"""
    _rocketmq_asyncio_shim(monkeypatch)
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    hooks: list[str] = []

    async def capture(name: str, payload: dict[str, Any]) -> None:
        hooks.append(name)

    async def boom(_: Any) -> None:
        raise RuntimeError("boom")

    envelope = build_envelope(
        "etl.task.done",
        {"case_id": "c1", "session_id": "s1", "backfill_date": "2026-09-27"},
        trace_id="tr_hook_2",
        event_id="evt_hook_2",
    )

    with (
        patch("common.eventbus.observability.on_event", new=AsyncMock(side_effect=capture)),
        patch.object(bus, "_send_to_dlq", new=AsyncMock(return_value=True)),
    ):
        await bus._handle_message(
            _msg(envelope.model_dump_json().encode("utf-8")),
            "etl.task.done",
            "gid_qc_etl_task_done",
            boom,
            SimpleNamespace(ack=lambda *a, **k: None),
            asyncio.get_running_loop(),
        )

    await bus.aclose()
    # maxRetryTimes=3 → 原始投递 + 3 次重试 = 4 次投递，每次失败各发一条 retry 钩子，
    # 耗尽后恰好一条 DLQ 钩子。
    assert hooks.count("csmp.eventbus.retry") == 4
    assert hooks.count("csmp.eventbus.dlq") == 1

"""阶段4：RocketMQ 单批有界并发消费单测（tasks 4.1/4.2/4.3/4.5）。

- 默认 parallelism=1：同批逐条串行（顺序、在途峰值=1），与改造前一致
- parallelism>1：同批 gather 并发，Semaphore 限制在途 handler 数 ≤ parallelism
- 单条失败隔离：失败条未 ack（Broker 重投），同批其余条正常 ack，循环继续
  （parallelism=1/4 两档同契约，锁定 N=1 下逃逸异常"仅 warn 续跑"的已评审行为）
- 真实重试路径：不 patch _handle_message——失败条 emit_retry+退避后重试成功，
  退避等待期间同批兄弟条照常确认（重试不阻塞同批），最终每条恰好 ack 一次
- factory：ROCKETMQ_CONSUMER_PARALLELISM 透传，未设/非法保守回退 1
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.eventbus.envelope import build_envelope
from common.eventbus.factory import get_event_bus, reset_event_bus
from common.eventbus.rocketmq import RocketMQEventBus

EVENT = "complaint.created"
GROUP = "gid_cs_complaint_created"


class _ScriptedConsumer:
    """脚本化 consumer：按批返回消息，批空后返回 [] 让循环空转。"""

    def __init__(self, batches: list[list[Any]]) -> None:
        self._batches = list(batches)
        self.acked: list[Any] = []

    def receive(self, *_a: Any, **_k: Any) -> list[Any]:
        if self._batches:
            return self._batches.pop(0)
        return []

    def ack(self, msg: Any, *_a: Any, **_k: Any) -> None:
        self.acked.append(msg)

    def shutdown(self) -> None:
        return None


def _msgs(n: int) -> list[MagicMock]:
    return [MagicMock(seq=i) for i in range(n)]


async def _run_loop(bus: RocketMQEventBus) -> asyncio.Task:
    """启动 consume loop（_start_consumer/_handle_message 由调用方 patch 覆盖）。"""
    return asyncio.create_task(bus._consume_loop(EVENT, GROUP, MagicMock()))


async def _stop(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def test_default_parallelism_is_one():
    """4.1：不传参默认 parallelism=1（等同现状串行）。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081")
    assert bus.parallelism == 1


@pytest.mark.asyncio
async def test_batch_gathered_concurrently_when_parallelism_four():
    """4.1/4.5：parallelism=4，4 条各 0.5s 的消息一批总耗时 ≈0.5s（非 2s 串行）。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081", parallelism=4)
    consumer = _ScriptedConsumer([_msgs(4)])
    done = asyncio.Event()
    inflight = 0
    max_inflight = 0

    async def fake_handle(msg, event_name, group, handler, consumer, loop):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.5)
        inflight -= 1
        consumer.ack(msg)
        if len(consumer.acked) == 4:
            done.set()

    with (
        patch.object(bus, "_handle_message", new=fake_handle),
        patch.object(bus, "_start_consumer", return_value=consumer),
    ):
        t0 = time.perf_counter()
        task = await _run_loop(bus)
        await asyncio.wait_for(done.wait(), timeout=5)
        elapsed = time.perf_counter() - t0
        await _stop(task)

    assert elapsed < 1.5, f"串行需 ~2s，实测 {elapsed:.2f}s 说明未并发"
    assert max_inflight == 4
    assert len(consumer.acked) == 4


@pytest.mark.asyncio
async def test_parallelism_one_preserves_serial_order():
    """4.1/4.3：parallelism=1 同批按原顺序逐条处理，在途峰值=1（零行为差异）。"""
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081", parallelism=1)
    consumer = _ScriptedConsumer([_msgs(4)])
    done = asyncio.Event()
    order: list[int] = []
    inflight = 0
    max_inflight = 0

    async def fake_handle(msg, event_name, group, handler, consumer, loop):
        nonlocal inflight, max_inflight
        inflight += 1
        max_inflight = max(max_inflight, inflight)
        await asyncio.sleep(0.05)
        order.append(msg.seq)
        inflight -= 1
        consumer.ack(msg)
        if len(consumer.acked) == 4:
            done.set()

    with (
        patch.object(bus, "_handle_message", new=fake_handle),
        patch.object(bus, "_start_consumer", return_value=consumer),
    ):
        task = await _run_loop(bus)
        await asyncio.wait_for(done.wait(), timeout=5)
        await _stop(task)

    assert order == [0, 1, 2, 3]
    assert max_inflight == 1
    assert len(consumer.acked) == 4


@pytest.mark.parametrize("parallelism", [1, 4])
@pytest.mark.asyncio
async def test_single_failure_isolated_and_loop_continues(parallelism):
    """4.2/4.5：批内一条处理异常上浮 → 该条未 ack（Broker 重投），

    其余条 ack=1，循环不退出、下一批照常处理。

    parallelism=1/4 两档锁定同一契约：N=1 时逃逸出 _handle_message 的异常
    （仅 ack/埋点等基础设施自身故障才可能发生，业务异常在内部已被捕获）
    同样只 warn 续跑、不杀消费 task——这是评审接受的与旧 for+await 路径的
    唯一行为差异（续跑比旧行为整个 task 终止更健壮），以测试固化而非保留双路径。
    """
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081", parallelism=parallelism)
    first_batch = _msgs(4)
    consumer = _ScriptedConsumer([first_batch])
    batch_done = asyncio.Event()
    second_done = asyncio.Event()
    warnings: list[dict[str, Any]] = []

    def warning_spy(event: str, **kwargs: Any) -> None:
        if event == "eventbus_consume_batch_error":
            warnings.append(kwargs)

    async def fake_handle(msg, event_name, group, handler, consumer, loop):
        if getattr(msg, "seq", None) == 1 and msg in first_batch:
            raise RuntimeError("boom-msg1")
        consumer.ack(msg)
        if msg in first_batch and len(consumer.acked) == 3:
            batch_done.set()
        if msg not in first_batch:
            second_done.set()

    with (
        patch.object(bus, "_handle_message", new=fake_handle),
        patch.object(bus, "_start_consumer", return_value=consumer),
        patch("common.eventbus.rocketmq.logger.warning", side_effect=warning_spy),
    ):
        task = await _run_loop(bus)
        await asyncio.wait_for(batch_done.wait(), timeout=5)

        # 失败条（seq=1）未 ack；成功条各 ack 一次
        acked_seqs = sorted(m.seq for m in consumer.acked)
        assert acked_seqs == [0, 2, 3]
        assert sum(1 for m in consumer.acked if m.seq == 0) == 1
        assert not any(m.seq == 1 for m in consumer.acked)

        # 异常已打 warn 且循环未退出 → 再投一批新消息仍可处理
        assert warnings and warnings[0]["error_type"] == "RuntimeError"
        assert not task.done()
        consumer._batches.append([MagicMock(seq=99)])
        await asyncio.wait_for(second_done.wait(), timeout=5)
        await _stop(task)

    assert any(m.seq == 99 for m in consumer.acked)


@pytest.mark.asyncio
async def test_real_handler_failure_retries_without_blocking_siblings():
    """4.2：不 patch _handle_message——失败条走完整真实重试循环。

    seq=1 首次 handler 抛异常 → emit_retry + 1s 退避（2**(1-1)）后重试成功；
    退避等待期间同批其余 3 条已全部确认（重试 sleep 不阻塞兄弟协程）；
    最终 4 条消息各自恰好 ack 一次，成功条各发一次 csmp.eventbus.consume。
    """
    bus = RocketMQEventBus(endpoints="127.0.0.1:18081", parallelism=4)

    # 真实信封体，使 _handle_message 走 parse→handler→重试→ack 完整路径
    batch: list[MagicMock] = []
    for seq in range(4):
        msg = MagicMock(seq=seq)
        msg.body = build_envelope(
            EVENT, {}, event_id=f"evt_parallel_retry_{seq}"
        ).model_dump_json().encode("utf-8")
        batch.append(msg)

    consumer = _ScriptedConsumer([batch])
    done = asyncio.Event()
    ack_at: dict[int, float] = {}
    _original_ack = consumer.ack

    def _record_ack(msg: Any, *_a: Any, **_k: Any) -> None:
        ack_at[msg.seq] = time.monotonic()
        _original_ack(msg)
        if len(consumer.acked) == 4:
            done.set()

    consumer.ack = _record_ack

    hooks: list[str] = []
    attempts = {"flaky": 0}

    async def _record_hook(name: str, _payload: dict[str, Any]) -> None:
        hooks.append(name)

    async def handler(envelope: Any) -> None:
        # seq=1：第一次失败、第二次成功（进一次真实重试路径）；其余直接成功
        if envelope.event_id == "evt_parallel_retry_1":
            attempts["flaky"] += 1
            if attempts["flaky"] == 1:
                raise RuntimeError("flaky-first-attempt")

    with (
        patch.object(bus, "_start_consumer", return_value=consumer),
        patch(
            "common.eventbus.observability.on_event",
            new=AsyncMock(side_effect=_record_hook),
        ),
    ):
        t0 = time.monotonic()
        task = await _run_loop_with_handler(bus, handler)
        await asyncio.wait_for(done.wait(), timeout=5)
        await _stop(task)

    # 失败条确实经过一次退避（首档 backoff=1s）后才确认
    assert attempts["flaky"] == 2
    assert ack_at[1] - t0 >= 0.8
    # 兄弟条在失败条退避窗口内即已确认（不被重试 sleep 阻塞）
    sibling_elapsed = [ack_at[s] - t0 for s in (0, 2, 3)]
    assert max(sibling_elapsed) < 0.5
    assert all(ack_at[s] < ack_at[1] for s in (0, 2, 3))
    # 每条恰好 ack 一次；重试恰好埋点一次、成功消费埋点 4 次
    assert sorted(m.seq for m in consumer.acked) == [0, 1, 2, 3]
    assert len(consumer.acked) == 4
    assert hooks.count("csmp.eventbus.retry") == 1
    assert hooks.count("csmp.eventbus.consume") == 4


async def _run_loop_with_handler(bus: RocketMQEventBus, handler: Any) -> asyncio.Task:
    """以真实 handler 启动 consume loop（仅 _start_consumer 被脚本化）。"""
    return asyncio.create_task(bus._consume_loop(EVENT, GROUP, handler))


@pytest.fixture(autouse=False)
def _clear_singleton():
    reset_event_bus()
    yield
    reset_event_bus()


@pytest.mark.usefixtures("_clear_singleton")
class TestFactoryParallelism:
    def test_default_one_when_unset(self, monkeypatch):
        monkeypatch.setenv("EVENT_BUS", "rocketmq")
        monkeypatch.delenv("ROCKETMQ_CONSUMER_PARALLELISM", raising=False)
        bus = get_event_bus()
        assert isinstance(bus, RocketMQEventBus)
        assert bus.parallelism == 1

    def test_read_from_env(self, monkeypatch):
        monkeypatch.setenv("EVENT_BUS", "rocketmq")
        monkeypatch.setenv("ROCKETMQ_CONSUMER_PARALLELISM", "4")
        bus = get_event_bus()
        assert bus.parallelism == 4

    @pytest.mark.parametrize("bad", ["abc", "0", "-3", "", "  "])
    def test_bad_value_falls_back_to_one(self, monkeypatch, bad):
        monkeypatch.setenv("EVENT_BUS", "rocketmq")
        if bad:
            monkeypatch.setenv("ROCKETMQ_CONSUMER_PARALLELISM", bad)
        else:
            monkeypatch.delenv("ROCKETMQ_CONSUMER_PARALLELISM", raising=False)
        bus = get_event_bus()
        assert bus.parallelism == 1

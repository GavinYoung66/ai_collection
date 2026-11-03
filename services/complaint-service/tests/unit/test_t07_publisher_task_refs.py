"""T07 回归：业务事件投递任务必须被强引用，完成前不被 GC 静默回收。"""
from __future__ import annotations

import asyncio
import gc

import pytest

from app.events.publisher import EventPublisher, pending_bus_publish_tasks


class _FakeBus:
    def __init__(self, release: asyncio.Event) -> None:
        self.release = release
        self.published: list[str] = []

    async def publish(self, topic, data, key=None, trace_id=None):  # noqa: ARG002
        await self.release.wait()
        self.published.append(topic)


@pytest.fixture()
def fake_bus(monkeypatch):
    release = asyncio.Event()
    bus = _FakeBus(release)
    monkeypatch.setattr("common.eventbus.factory.get_event_bus", lambda: bus)
    return bus


@pytest.mark.asyncio
async def test_task_tracked_until_done(fake_bus):
    pub = EventPublisher()
    pub.publish("complaint.judged", {"case_id": "t07-1", "trace_id": "tr"})
    await asyncio.sleep(0)
    assert pending_bus_publish_tasks() >= 1

    fake_bus.release.set()
    await _wait_tasks_done()
    assert pending_bus_publish_tasks() == 0
    assert fake_bus.published == ["complaint.judged"]


@pytest.mark.asyncio
async def test_tasks_survive_gc_and_all_events_delivered(fake_bus):
    """GC 压力下投递任务不得丢失：全部事件最终送达且引用集合清空。"""
    pub = EventPublisher()
    for i in range(20):
        pub.publish("complaint.judged", {"case_id": f"t07-gc-{i}", "trace_id": f"tr-{i}"})

    await asyncio.sleep(0)
    gc.collect()
    await asyncio.sleep(0)
    gc.collect()
    assert pending_bus_publish_tasks() == 20

    fake_bus.release.set()
    await _wait_tasks_done()
    assert pending_bus_publish_tasks() == 0
    assert len(fake_bus.published) == 20


@pytest.mark.asyncio
async def test_hook_events_do_not_spawn_bus_task(fake_bus):
    pub = EventPublisher()
    pub.publish("csmp.degrade.open", {"trace_id": "tr"})
    await asyncio.sleep(0)
    assert pending_bus_publish_tasks() == 0
    assert fake_bus.published == []


async def _wait_tasks_done(timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while pending_bus_publish_tasks() > 0:
        if loop.time() > deadline:
            raise AssertionError("业务事件投递任务未在超时内结束")
        await asyncio.sleep(0.01)

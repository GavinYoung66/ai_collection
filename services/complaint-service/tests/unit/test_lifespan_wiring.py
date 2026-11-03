"""T25：FastAPI lifespan 不再承载常驻循环；worker 独立负责后台任务。"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app

_APP_DIR = Path(__file__).resolve().parents[2] / "app"


def test_main_source_has_no_resident_loop_wiring() -> None:
    """源码级钉住：HTTP 入口不再 create_task / 订阅 EventBus。"""
    source = (_APP_DIR / "main.py").read_text(encoding="utf-8")
    assert "asyncio.create_task" not in source
    assert "start_eventbus_subscriber" not in source
    assert "queue_consumer_loop" not in source

    worker_source = (_APP_DIR / "worker.py").read_text(encoding="utf-8")
    for name in (
        "queue_consumer_loop",
        "maintenance_loop",
        "deferred_consumer_loop",
        "start_eventbus_subscriber",
    ):
        assert name in worker_source


def test_lifespan_starts_no_resident_loops() -> None:
    """HTTP 进程的 lifespan 只装配请求期资源，不创建任何后台任务。"""
    with TestClient(app) as client:
        state = client.app.state
        assert getattr(state, "queue_consumer", None) is None
        assert getattr(state, "maintenance", None) is None
        assert getattr(state, "deferred_consumer", None) is None
        assert getattr(state, "eventbus_subscriber", None) is None
        assert client.get("/health").status_code == 200


@pytest.mark.asyncio
async def test_worker_background_tasks_are_named_and_cancellable(monkeypatch) -> None:
    """worker 能独立创建三组常驻任务，并在停止时干净退出。"""
    import app.worker as worker

    async def _idle_loop() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "queue_consumer_loop", _idle_loop)
    monkeypatch.setattr(worker, "maintenance_loop", _idle_loop)
    monkeypatch.setattr(worker, "deferred_consumer_loop", _idle_loop)

    async def _no_eventbus():
        return None

    monkeypatch.setattr("app.events.subscriber.start_eventbus_subscriber", _no_eventbus)
    monkeypatch.setattr(worker.settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", True)

    tasks, eventbus_task = await worker.start_background_tasks()
    assert eventbus_task is None
    names = {task.get_name() for task in tasks}
    assert names == {
        "cs-queue-consumer",
        "cs-maintenance",
        "cs-deferred-consumer",
    }
    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_run_worker_stops_on_event(monkeypatch) -> None:
    """run_worker 在收到停止事件后完成启动/关停闭环（不依赖真实信号）。"""
    import app.worker as worker

    async def _idle_loop() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(worker, "queue_consumer_loop", _idle_loop)
    monkeypatch.setattr(worker, "maintenance_loop", _idle_loop)

    async def _no_eventbus():
        return None

    monkeypatch.setattr("app.events.subscriber.start_eventbus_subscriber", _no_eventbus)
    monkeypatch.setattr(worker.settings, "COMPLAINT_DEFERRED_QUEUE_ENABLED", False)
    monkeypatch.setattr(worker, "_startup", _no_startup)
    monkeypatch.setattr(worker, "_shutdown", _no_shutdown)

    stop = asyncio.Event()
    task = asyncio.create_task(worker.run_worker(stop))
    await asyncio.sleep(0)
    stop.set()
    await asyncio.wait_for(task, timeout=1.0)


async def _no_startup() -> None:
    return None


async def _no_shutdown(tasks, eventbus_task) -> None:  # noqa: ARG001
    for task in tasks:
        task.cancel()
    for task in tasks:
        with pytest.raises(asyncio.CancelledError):
            await task

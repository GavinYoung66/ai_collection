"""common.resilience.hooks 单测。"""
import pytest

from common.resilience import hooks as hooks_mod
from common.resilience.hooks import on_event, set_publisher


@pytest.fixture(autouse=True)
def _reset_publisher():
    set_publisher(None)
    yield
    set_publisher(None)


@pytest.mark.asyncio
async def test_no_publisher_logs_without_raise(monkeypatch):
    # 默认 structlog 日志，无 publisher 不报错，且事件确实被记录
    calls = []

    class FakeLogger:
        def info(self, event, **kwargs):
            calls.append((event, kwargs))

        def warning(self, event, **kwargs):
            calls.append((event, kwargs))

    monkeypatch.setattr(hooks_mod, "logger", FakeLogger())
    await on_event("csmp.rate_limit.hit", {"key": "k", "limit": 20})

    assert calls == [(
        "resilience_event",
        {"name": "csmp.rate_limit.hit", "payload": {"key": "k", "limit": 20}},
    )]


@pytest.mark.asyncio
async def test_publisher_receives_event():
    received = []

    async def publisher(name, payload):
        received.append((name, payload))

    set_publisher(publisher)
    await on_event("qc.queue.full", {"max_len": 100})

    assert received == [("qc.queue.full", {"max_len": 100})]


@pytest.mark.asyncio
async def test_publisher_failure_is_swallowed(monkeypatch):
    warnings = []

    class FakeLogger:
        def info(self, event, **kwargs):
            pass

        def warning(self, event, **kwargs):
            warnings.append((event, kwargs))

    async def bad_publisher(name, payload):
        raise RuntimeError("publisher down")

    set_publisher(bad_publisher)
    monkeypatch.setattr(hooks_mod, "logger", FakeLogger())
    # publisher 抛错降级回 structlog warning，不影响调用方
    await on_event("csmp.circuit_breaker.open", {"key": "csmp:llm_degraded:m1"})

    assert warnings == [(
        "hook_publisher_failed",
        {"name": "csmp.circuit_breaker.open", "error": "publisher down"},
    )]


@pytest.mark.asyncio
async def test_clear_publisher_restores_default():
    async def publisher(name, payload):
        raise AssertionError("不应被调用")

    set_publisher(publisher)
    set_publisher(None)
    await on_event("rag.rate_limit.degraded", {"key": "k"})


@pytest.mark.parametrize("bad_name", [
    "csmp",                # 只有 1 段
    "csmp..hit",           # 空段
    "CSMP.ratelimit.hit",  # 大写
    "",
])
@pytest.mark.asyncio
async def test_invalid_event_name_rejected(bad_name):
    with pytest.raises(ValueError):
        await on_event(bad_name, {})


@pytest.mark.asyncio
async def test_two_segment_event_name_allowed():
    # spec 示例 complaint.degraded 为 2 段 {svc}.{action}，须合法
    await on_event("complaint.degraded", {"reason": "llm"})

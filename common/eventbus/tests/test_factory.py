"""EventBus factory tests — runtime accepts only ``rocketmq``."""
from __future__ import annotations

import pytest

from common.eventbus.factory import get_event_bus, reset_event_bus
from common.eventbus.rocketmq import RocketMQEventBus


@pytest.fixture(autouse=True)
def _clear_singleton():
    reset_event_bus()
    yield
    reset_event_bus()


def test_factory_rocketmq(monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "rocketmq")
    bus = get_event_bus()
    assert isinstance(bus, RocketMQEventBus)
    assert get_event_bus() is bus


@pytest.mark.parametrize("mode", ["redis", "redis_streams", "foo", ""])
def test_factory_illegal_raises(monkeypatch, mode):
    if mode == "":
        monkeypatch.delenv("EVENT_BUS", raising=False)
    else:
        monkeypatch.setenv("EVENT_BUS", mode)
    with pytest.raises(ValueError, match="EVENT_BUS"):
        get_event_bus()


def test_factory_empty_raises(monkeypatch):
    monkeypatch.delenv("EVENT_BUS", raising=False)
    with pytest.raises(ValueError):
        get_event_bus()

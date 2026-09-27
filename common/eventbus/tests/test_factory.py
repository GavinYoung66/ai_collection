"""EventBus factory tests (task 2.5)."""
from __future__ import annotations

import pytest

from common.eventbus.factory import get_event_bus, reset_event_bus
from common.eventbus.redis_pubsub import RedisPubSubEventBus
from common.eventbus.redis_streams import RedisStreamsEventBus
from common.eventbus.rocketmq import RocketMQEventBus


@pytest.fixture(autouse=True)
def _clear_singleton():
    reset_event_bus()
    yield
    reset_event_bus()


def test_factory_redis(monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "redis")
    bus = get_event_bus()
    assert isinstance(bus, RedisPubSubEventBus)
    assert get_event_bus() is bus


def test_factory_rocketmq(monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "rocketmq")
    bus = get_event_bus()
    assert isinstance(bus, RocketMQEventBus)


def test_factory_redis_streams(monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "redis_streams")
    bus = get_event_bus()
    assert isinstance(bus, RedisStreamsEventBus)


def test_factory_illegal_raises(monkeypatch):
    monkeypatch.setenv("EVENT_BUS", "foo")
    with pytest.raises(ValueError, match="EVENT_BUS"):
        get_event_bus()


def test_factory_empty_raises(monkeypatch):
    monkeypatch.delenv("EVENT_BUS", raising=False)
    with pytest.raises(ValueError):
        get_event_bus()

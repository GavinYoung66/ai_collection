"""EventBus Protocol structural checks.

Runtime factory only loads RocketMQ. Redis adapters are reference-only and are
checked here solely for Protocol structural compatibility.
"""
from __future__ import annotations

from common.eventbus.base import EventBus, consumer_group_name
from common.eventbus.redis_pubsub import RedisPubSubEventBus
from common.eventbus.redis_streams import RedisStreamsEventBus
from common.eventbus.rocketmq import RocketMQEventBus


def test_consumer_group_naming():
    assert (
        consumer_group_name("qc", "call.session.ended")
        == "gid_qc_call_session_ended"
    )


def test_rocketmq_adapter_satisfies_protocol():
    assert isinstance(RocketMQEventBus(), EventBus)
    assert callable(getattr(RocketMQEventBus, "publish"))
    assert callable(getattr(RocketMQEventBus, "subscribe"))


def test_reference_adapters_still_satisfy_protocol():
    """Historical adapters remain Protocol-compatible; not factory-loaded."""
    for cls in (RedisPubSubEventBus, RedisStreamsEventBus):
        assert isinstance(cls(), EventBus)

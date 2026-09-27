"""EventBus Protocol structural checks (task 2.2)."""
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


def test_three_adapters_satisfy_protocol():
    for cls in (RedisPubSubEventBus, RocketMQEventBus, RedisStreamsEventBus):
        assert isinstance(cls(), EventBus)
        assert callable(getattr(cls, "publish"))
        assert callable(getattr(cls, "subscribe"))

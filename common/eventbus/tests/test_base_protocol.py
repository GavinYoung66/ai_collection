"""EventBus Protocol structural checks（T31 后仅剩生产运行模式 RocketMQEventBus）。

Redis 双适配器（redis_pubsub / redis_streams）已作为死代码删除；
factory 仅加载 rocketmq，非法 EVENT_BUS 值由 test_factory 覆盖拒绝路径。
"""
from __future__ import annotations

from common.eventbus.base import EventBus, consumer_group_name
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

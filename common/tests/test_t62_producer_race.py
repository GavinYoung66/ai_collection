"""T62 / 6.1：_ensure_producer 跨线程竞态消除 单测。

验收判据：2 线程并发首发，Producer 构造次数为 1（monkeypatch 计数），
无被覆盖实例泄漏底层连接（两线程取得同一实例）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import sys
import threading
import types

from common.eventbus.rocketmq import RocketMQEventBus


def test_concurrent_first_start_constructs_producer_once(monkeypatch):
    """2 线程 barrier 同步并发首发：构造恰好 1 次、同实例返回。"""
    fake = types.ModuleType("rocketmq")
    constructed: list = []

    class FakeProducer:
        def __init__(self, conf):
            constructed.append(self)
            self.startup_called = False

        def startup(self):
            self.startup_called = True

    fake.ClientConfiguration = lambda *args, **kwargs: object()
    fake.Credentials = lambda: object()
    fake.Producer = FakeProducer
    monkeypatch.setitem(sys.modules, "rocketmq", fake)

    bus = RocketMQEventBus(endpoints="127.0.0.1:8081")
    results: list = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()  # 两线程同时进入，最大化 check-then-act 竞态窗口
        results.append(bus._ensure_producer())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(constructed) == 1, (
        f"并发首发 Producer 被构造 {len(constructed)} 次（被覆盖实例泄漏底层连接）"
    )
    assert len(results) == 2
    assert results[0] is results[1], "两线程应取得同一 Producer 实例"
    assert constructed[0].startup_called


def test_repeated_calls_return_same_instance(monkeypatch):
    """同线程重复调用：命中已构造实例，不重复构造（回归）。"""
    fake = types.ModuleType("rocketmq")
    constructed: list = []

    class FakeProducer:
        def __init__(self, conf):
            constructed.append(self)

        def startup(self):
            pass

    fake.ClientConfiguration = lambda *args, **kwargs: object()
    fake.Credentials = lambda: object()
    fake.Producer = FakeProducer
    monkeypatch.setitem(sys.modules, "rocketmq", fake)

    bus = RocketMQEventBus(endpoints="127.0.0.1:8081")
    p1 = bus._ensure_producer()
    p2 = bus._ensure_producer()
    assert p1 is p2
    assert len(constructed) == 1
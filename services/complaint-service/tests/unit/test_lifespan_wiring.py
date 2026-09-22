"""GAP-3：lifespan 集成测试（TestClient with 块真实触发 startup/shutdown）。

覆盖此前未经验证的装配面：
- startup：wire_resilience_hooks 生效（hooks publisher 非默认 None）、
  排队消费循环 task 已创建并在运行；
- 运行期：后台消费循环与 HTTP 请求共存互不干扰；
- shutdown：消费循环取消、在途任务集合清空、hooks 恢复默认 structlog。

注：TestClient/ASGITransport 直接发起请求均不触发 lifespan，只有
``with TestClient(app)`` 上下文形式会执行 startup/shutdown。
"""
from __future__ import annotations

import time

from fastapi.testclient import TestClient

from app.events.publisher import publisher
from app.main import _running_queued, app
from common.resilience import hooks


def test_lifespan_wires_hooks_and_manages_consumer_loop():
    publisher.clear()
    try:
        with TestClient(app) as client:
            # startup 完成：G7 hooks 已接到本服务 publisher
            assert hooks.get_publisher() is not None
            # G8 消费循环 task 已创建且在运行
            consumer = app.state.queue_consumer
            assert consumer is not None
            assert not consumer.done()

            # 后台循环每秒空转一轮，与 HTTP 请求共存互不干扰
            r = client.get("/api/v1/health")
            assert r.status_code == 200
            time.sleep(0.05)  # 让出 portal 线程至少一个调度片
            assert not consumer.done()

        # shutdown：消费循环已取消、在途集合清空、hooks 恢复默认
        assert consumer.cancelled()
        assert len(_running_queued) == 0
        assert hooks.get_publisher() is None
    finally:
        # 双保险：断言失败路径也不污染全局 hooks/publisher
        hooks.set_publisher(None)
        publisher.clear()

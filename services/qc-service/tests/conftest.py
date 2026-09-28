"""qc-service 测试：把服务根加入 sys.path。"""
import os
import sys
from pathlib import Path

import pytest

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

os.environ["QC_SKIP_BACKGROUND"] = "1"


@pytest.fixture(autouse=True)
def _reset_shared_ac():
    from app.services.ac_filter import reset_shared_ac

    reset_shared_ac()
    yield
    reset_shared_ac()


@pytest.fixture(autouse=True)
def _reset_qc_resilience_state():
    from app.integrations.rag_client import reset_rag_search_breaker
    from app.services.inflight import reset_inflight_gate
    from common.resilience._redis import reset_redis

    # resilience 共享的 async Redis 客户端在首次使用时绑定当前事件循环；
    # pytest-asyncio 每个用例使用独立 function 级事件循环，不重置会复用到
    # 已关闭循环的连接池（got Future attached to a different loop）。
    reset_rag_search_breaker()
    reset_inflight_gate()
    reset_redis()
    yield
    reset_rag_search_breaker()
    reset_inflight_gate()
    reset_redis()


@pytest.fixture(autouse=True)
def _mock_event_bus(monkeypatch):
    """跨服务 EventBus 一律 mock：单测不连真实 RocketMQ。

    publisher/subscriber 均为函数内 ``from common.eventbus.factory import
    get_event_bus``，每次调用从源模块取属性，patch 源模块即生效。
    """
    from unittest.mock import AsyncMock

    bus = AsyncMock()
    monkeypatch.setattr(
        "common.eventbus.factory.get_event_bus", lambda **kwargs: bus
    )
    yield bus


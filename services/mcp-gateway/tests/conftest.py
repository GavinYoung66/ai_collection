"""mcp-gateway 测试基座：sys.path 装配 + Redis/熔断/事件钩子隔离。"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SERVICE_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
for _p in (str(REPO_ROOT), str(SERVICE_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from common.llm.reliability import set_default_service  # noqa: E402
from common.mcp import reliability as mcp_mod  # noqa: E402
from common.resilience._redis import configure_redis, reset_redis  # noqa: E402
from common.resilience.hooks import set_publisher  # noqa: E402


class FakeRedis:
    """内存版 Redis：覆盖限流（set nx/incr/expire）+ 降级标志（setex/exists）
    + 回写零丢失入队（lpush）。"""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.counts: dict[str, int] = {}
        self.lists: dict[str, list[str]] = {}

    async def set(self, key, value, ex=None, nx=False):  # noqa: ARG002 — 对齐 redis 接口
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):  # noqa: ARG002 — 对齐 redis 接口
        return None

    async def exists(self, key):
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):  # noqa: ARG002 — 对齐 redis 接口
        self.store[key] = value

    async def delete(self, key):
        self.store.pop(key, None)

    async def lpush(self, key, value):
        self.lists.setdefault(key, []).insert(0, value)
        return len(self.lists[key])


@pytest.fixture(autouse=True)
def isolate():
    """每用例隔离：FakeRedis + csmp 服务名 + 清空熔断注册表 + 捕获 hook 事件。"""
    fake = FakeRedis()
    configure_redis(fake)
    set_default_service("csmp")
    mcp_mod._mcp_breakers.clear()
    events: list[tuple[str, dict]] = []

    async def _pub(name, payload):
        events.append((name, payload))

    set_publisher(_pub)
    yield SimpleNamespace(redis=fake, events=events)
    set_publisher(None)
    reset_redis()
    mcp_mod._mcp_breakers.clear()
    set_default_service(None)

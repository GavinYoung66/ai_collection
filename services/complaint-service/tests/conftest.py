"""complaint-service 测试 conftest。

把服务根加入 sys.path；单测默认强制 Mock，避免本机 .env 的 USE_MOCK_*=false
误打真 LLM/MCP（真实路径用例自行 monkeypatch）。
"""
import sys
from pathlib import Path

import pytest

# 服务根：services/complaint-service（conftest 在 tests/ 下，上溯一级）
_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

# 仓库根 complaint-service/app（含 models.state Schema）
_REPO_COMPLAINT_APP = _SERVICE_ROOT.parent.parent / "complaint-service" / "app"
if _REPO_COMPLAINT_APP.exists() and str(_REPO_COMPLAINT_APP) not in sys.path:
    sys.path.insert(0, str(_REPO_COMPLAINT_APP))


@pytest.fixture(autouse=True)
def _default_mock_flags(monkeypatch):
    from app.api.deps import reset_llm_provider, reset_mcp_client
    from app.config import settings

    monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
    monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
    reset_llm_provider()
    reset_mcp_client()
    yield
    reset_llm_provider()
    reset_mcp_client()


class _GlobalFakeRedis:
    """路由级韧性链路（幂等锁/限流/deadline/degraded flag）默认内存 Redis。

    G8 接线后 judge 路由走 acquire_complaint（SETNX/INCR/GET/DELETE），
    未配置 Redis 时会懒建真连接吃 socket 超时；默认注入内存实现保证
    单测快速确定性。需要自定义行为的用例自行 configure_redis 覆盖。
    """

    def __init__(self):
        self.store: dict[str, str] = {}
        self.counts: dict[str, int] = {}

    async def set(self, key, value, nx=False, ex=None):  # noqa: ARG002
        if nx and key in self.store:
            return None
        self.store[key] = str(value)
        return True

    async def setex(self, key, ttl, value):  # noqa: ARG002
        self.store[key] = str(value)
        return True

    async def get(self, key):
        return self.store.get(key)

    async def delete(self, *keys):
        return sum(1 for k in keys if self.store.pop(k, None) is not None)

    async def ping(self):
        return True

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key, window):  # noqa: ARG002
        return None


@pytest.fixture(autouse=True)
def _global_fake_redis():
    from common.resilience._redis import configure_redis, reset_redis

    configure_redis(_GlobalFakeRedis())
    yield
    reset_redis()

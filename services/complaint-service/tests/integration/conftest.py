"""集成测试 conftest：FastAPI TestClient + USE_REAL_INFRA 切换。

默认 USE_REAL_INFRA=0：FakeRedis + Mock PG/LLM/MCP。
USE_REAL_INFRA=1 时使用 docker-compose.dev.yml 提供的真 PG+Redis。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# nest_asyncio 全局 apply() 会 monkeypatch Task 类与 asyncio.run，污染整个测试
# 进程的事件循环（尤其让 anyio 流关闭时的 CancelledError 吞并场景下 wait_for
# 不超时）。app 侧 asyncio.run 调用点均已带 get_running_loop() 守卫，无需嵌套 run。
# 若某 TestClient 场景确需嵌套 run，再按需局部启用。
# nest_asyncio.apply()

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


@pytest.fixture
def use_real_infra() -> bool:
    return os.environ.get("USE_REAL_INFRA", "0") == "1"

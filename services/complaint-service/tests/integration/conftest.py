"""集成测试 conftest：FastAPI TestClient + USE_REAL_INFRA 切换。

默认 USE_REAL_INFRA=0：FakeRedis + Mock PG/LLM/MCP。
USE_REAL_INFRA=1 时使用 docker-compose.dev.yml 提供的真 PG+Redis。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import nest_asyncio
import pytest
from fastapi.testclient import TestClient

nest_asyncio.apply()  # 允许 TestClient 内嵌套 asyncio.run（judge run_graph=True 路径）

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

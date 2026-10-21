"""E2E conftest：复用 integration 的 TestClient（不再 nest_asyncio）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 见 tests/integration/conftest.py：全局 nest_asyncio.apply() 污染测试进程事件
# 循环（Task 类与 asyncio.run 被 monkeypatch），已移除；app 侧 asyncio.run 均有守卫。
# nest_asyncio.apply()

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

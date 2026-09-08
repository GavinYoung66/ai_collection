"""E2E conftest：复用 integration 的 TestClient + nest_asyncio。"""
from __future__ import annotations

import sys
from pathlib import Path

import nest_asyncio
import pytest
from fastapi.testclient import TestClient

nest_asyncio.apply()

_SERVICE_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))

from app.main import app  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)

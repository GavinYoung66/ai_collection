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

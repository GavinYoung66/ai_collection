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

    reset_rag_search_breaker()
    reset_inflight_gate()
    yield
    reset_rag_search_breaker()
    reset_inflight_gate()


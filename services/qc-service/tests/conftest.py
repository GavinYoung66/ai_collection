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


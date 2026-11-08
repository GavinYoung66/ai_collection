"""鉴权矩阵覆盖完整性守护（4.1）：四服务矩阵测试资产存在性 + 收口路由登记核对。

矩阵扫描本体在各服务 tests/test_auth_matrix.py（同进程跨服务导入 app 存在包名
冲突，故按服务落位——与"repo 级资产"建议的偏差在 4.3 状态说明留痕）。本文件
守护资产本身：文件存在 + 本批 9 组收口路由均已登记（文本级核对）。
"""
from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

_MATRIX_FILES = {
    "qc-service": _REPO_ROOT / "services/qc-service/tests/test_auth_matrix.py",
    "complaint-service": _REPO_ROOT / "services/complaint-service/tests/unit/test_auth_matrix.py",
    "rag-service": _REPO_ROOT / "services/rag-service/tests/test_auth_matrix.py",
    "data-platform": _REPO_ROOT / "services/data-platform/tests/test_auth_matrix.py",
}

# 本批 9 组收口路由登记（按服务）：矩阵文件源码必须包含这些登记
_REQUIRED_REGISTRATIONS = {
    "qc-service": [
        "/api/v1/qc/demo/seed",
        "/api/v1/qc/demo/pending",
        "/api/v1/qc/demo/{session_id}/trace",
        "/api/v1/qc/replay/raw",
        "/api/v1/qc/replay/{session_id}",
        "/api/v1/qc/check/batch",
    ],
    "complaint-service": [
        "/api/v1/complaints/mode",
        "/api/v1/complaints/stats",
        "/api/v1/complaints/consistency",
    ],
    "rag-service": [
        "/api/v1/kb/documents/{doc_id}",
        "/api/v1/sensitive-words/current.dict",
    ],
    "data-platform": [
        "/api/v1/data/sessions/{session_id}/qc-source",
        "/api/v1/data/sessions:pending-qc",
    ],
}


class TestMatrixAssets:
    def test_all_service_matrix_files_exist(self):
        missing = [svc for svc, p in _MATRIX_FILES.items() if not p.exists()]
        assert missing == [], f"missing matrix guard files: {missing}"

    def test_batch123_routes_registered_in_matrices(self):
        missing = []
        for svc, routes in _REQUIRED_REGISTRATIONS.items():
            source = _MATRIX_FILES[svc].read_text(encoding="utf-8")
            for route in routes:
                if route not in source:
                    missing.append(f"{svc}: {route}")
        assert missing == [], f"routes not registered in matrix: {missing}"
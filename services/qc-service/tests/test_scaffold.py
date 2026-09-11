"""QC-1.1：目录树、无 WS、无平行业务 api、无 ingest。"""
from pathlib import Path

from fastapi.testclient import TestClient
from starlette.routing import WebSocketRoute

from app.integrations.rag_client import RagClient
from app.main import app

_APP_ROOT = Path(__file__).resolve().parents[1] / "app"

_SECTION3_FILES = [
    "main.py",
    "routers/check.py",
    "routers/records.py",
    "routers/alerts.py",
    "routers/review.py",
    "routers/rules.py",
    "services/offline_qc_service.py",
    "services/replay_service.py",
    "services/ac_filter.py",
    "services/rule_engine.py",
    "services/llm_reviewer.py",
    "services/scoring_service.py",
    "services/alert_service.py",
    "services/review_service.py",
    "services/word_loader.py",
    "repositories/quality_repo.py",
    "repositories/alert_repo.py",
    "repositories/review_repo.py",
    "repositories/rule_repo.py",
    "events/publisher.py",
    "events/subscriber.py",
    "integrations/rag_client.py",
    "integrations/dp_client.py",
    "integrations/guardrails_adapter.py",
]


class TestScaffoldTree:
    def test_section3_directories_exist(self):
        for name in ("routers", "services", "repositories", "events", "integrations"):
            assert (_APP_ROOT / name).is_dir(), name

    def test_section3_files_exist(self):
        missing = [rel for rel in _SECTION3_FILES if not (_APP_ROOT / rel).is_file()]
        assert missing == []

    def test_no_websocket_module(self):
        assert not (_APP_ROOT / "websocket").exists()
        assert not (_APP_ROOT / "ws").exists()
        hits = [p.name for p in _APP_ROOT.rglob("*") if "websocket" in p.name.lower()]
        assert hits == []

    def test_api_package_is_health_only(self):
        api_py = sorted(p.name for p in (_APP_ROOT / "api").glob("*.py"))
        assert api_py == ["__init__.py", "health.py"]

    def test_no_second_business_api_tree(self):
        business_like = list((_APP_ROOT / "api").glob("*.py"))
        names = {p.name for p in business_like} - {"__init__.py", "health.py"}
        assert names == set()


class TestNoRealtimeSurface:
    def test_no_websocket_routes(self):
        assert not any(isinstance(route, WebSocketRoute) for route in app.router.routes)

    def test_no_ingest_or_websocket(self):
        paths = [getattr(route, "path", "") for route in app.router.routes]
        assert not any(isinstance(route, WebSocketRoute) for route in app.router.routes)
        assert any("/api/v1/qc/replay/{session_id}" in path or "/replay/" in path for path in paths)

    def test_post_qc_check_not_ingest(self):
        client = TestClient(app)
        for path in ("/qc/check", "/api/v1/qc/check"):
            resp = client.post(path)
            assert resp.status_code in {404, 405}

    def test_health_not_under_api_v1(self):
        client = TestClient(app)
        assert client.get("/api/v1/health").status_code == 404

    def test_rag_client_has_no_search(self):
        assert not hasattr(RagClient, "search")
        assert not hasattr(RagClient, "kb_search")
        assert "search" not in vars(RagClient)

    def test_dockerfile_healthcheck_is_root_health(self):
        text = Path(__file__).resolve().parents[1].joinpath("Dockerfile").read_text(encoding="utf-8")
        assert "HEALTHCHECK" in text
        assert "http://localhost:8000/health" in text
        assert "/api/v1/health" not in text
        assert "EXPOSE 8000" in text
        assert '"--port", "8000"' in text
        assert "/kb/search" not in text

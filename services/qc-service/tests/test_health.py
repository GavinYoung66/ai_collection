"""QC-10.1 / QC-6.5：服务根 GET /health 信封 + 三字段。"""
from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.deps import get_rag_client, get_redis
from app.main import app
from app.observability import captured_events, reset_events
from app.core.config import qc_settings
from app.repositories.job_store import KEY_WORDS_VERSION, llm_chain_models, llm_degraded_key
from common.llm import reliability as reliability_mod
from tests.test_job_store import MemoryRedis


@pytest.fixture(autouse=True)
def _clear_llm_breakers():
    reliability_mod._llm_breakers.clear()
    yield
    reliability_mod._llm_breakers.clear()


class FakeRag:
    def __init__(self, *, connected: bool = True, words: list[str] | None = None):
        self.connected = connected
        self.words = words if words is not None else ["起诉"]
        self.ac_ref = tuple(self.words) if self.words else None
        self.breaker_open = False

    async def probe_dict_reachable(self, timeout: float = 1.0) -> bool:
        return self.connected


def _health_client(redis: MemoryRedis, rag: FakeRag) -> TestClient:
    async def _redis():
        return redis

    app.dependency_overrides[get_redis] = _redis
    app.dependency_overrides[get_rag_client] = lambda: rag
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(get_redis, None)
    app.dependency_overrides.pop(get_rag_client, None)


class TestHealthEnvelope:
    def teardown_method(self):
        _cleanup()

    def test_root_health_envelope_and_three_fields(self):
        redis = MemoryRedis(db=3)
        redis.data[KEY_WORDS_VERSION] = "3"
        client = _health_client(redis, FakeRag(connected=True))
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert set(body) == {"code", "message", "data", "trace_id"}
        assert body["code"] == "0"
        data = body["data"]
        assert data["status"] in {"ok", "healthy"}
        assert data["ac_version"] == "3"
        assert data["llm_degraded"] is False
        assert data["rag_connected"] is True
        assert "unhealthy" not in data
        assert "ac_stale" not in data
        assert "llm_open" not in data
        assert body["code"] not in {"A2001", "A2002", "A2003", "A2005", "A2011"}

    def test_primary_only_open_llm_degraded_false(self):
        reliability_mod._llm_breakers.clear()
        redis = MemoryRedis(db=3)
        redis.data[KEY_WORDS_VERSION] = "3"
        redis.data[llm_degraded_key(qc_settings.QC_LLM_MODEL)] = "1"
        client = _health_client(redis, FakeRag(connected=True))
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["llm_degraded"] is False
        assert body["data"]["status"] in {"ok", "healthy"}
        assert "unhealthy" not in body["data"]

    def test_whole_chain_open_llm_degraded_true(self):
        reliability_mod._llm_breakers.clear()
        redis = MemoryRedis(db=3)
        redis.data[KEY_WORDS_VERSION] = "3"
        redis.data[llm_degraded_key(qc_settings.QC_LLM_MODEL)] = "1"
        redis.data[llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])] = "1"
        client = _health_client(redis, FakeRag(connected=True))
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        assert body["data"]["llm_degraded"] is True
        assert body["data"]["status"] == "degraded"
        assert "unhealthy" not in body["data"]

    def test_health_not_mounted_under_api_v1(self):
        redis = MemoryRedis(db=3)
        client = _health_client(redis, FakeRag())
        resp = client.get("/api/v1/health")
        assert resp.status_code == 404

    def test_health_trace_id_echo(self):
        redis = MemoryRedis(db=3)
        redis.data[KEY_WORDS_VERSION] = "3"
        client = _health_client(redis, FakeRag())
        resp = client.get("/health", headers={"X-Trace-Id": "trace_test"})
        assert resp.headers["X-Trace-Id"] == "trace_test"
        assert resp.json()["trace_id"] == "trace_test"

    def test_missing_trace_id_is_generated_and_echoed(self):
        redis = MemoryRedis(db=3)
        redis.data[KEY_WORDS_VERSION] = "3"
        client = _health_client(redis, FakeRag())
        resp = client.get("/health")
        assert resp.status_code == 200
        header = resp.headers.get("X-Trace-Id")
        assert header
        assert resp.json()["trace_id"] == header
        assert resp.json()["code"] == "0"

    def test_rag_disconnected_is_degraded_without_stale_event(self):
        reset_events()
        redis = MemoryRedis(db=3)
        redis.data[KEY_WORDS_VERSION] = "3"
        client = _health_client(redis, FakeRag(connected=False))
        resp = client.get("/health")
        body = resp.json()
        assert resp.status_code == 200
        assert body["code"] == "0"
        assert body["data"]["rag_connected"] is False
        assert body["data"]["status"] == "degraded"
        assert "unhealthy" not in body["data"]
        assert "ac_stale" not in body["data"]
        names = {e["event"] for e in captured_events()}
        assert "rag.words.stale" not in names
        assert "rag.words.loaded" not in names

    @pytest.mark.asyncio
    async def test_redis_error_falls_back_to_process_chain_open(self):
        from app.api.health import _probe_health

        for model in llm_chain_models():
            breaker = reliability_mod._llm_breakers.get_or_create(llm_degraded_key(model))
            for _ in range(3):
                await breaker.record_failure()
            assert breaker.state == "open"

        class BoomRedis(MemoryRedis):
            async def get(self, key):
                raise OSError("redis down")

        data = await _probe_health(BoomRedis(db=3), FakeRag(connected=True))
        assert data["llm_degraded"] is True
        assert data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_redis_error_without_open_breakers_llm_degraded_false(self):
        from app.api.health import _probe_health

        class BoomRedis(MemoryRedis):
            async def get(self, key):
                raise OSError("redis down")

        data = await _probe_health(BoomRedis(db=3), FakeRag(connected=True))
        assert data["llm_degraded"] is False

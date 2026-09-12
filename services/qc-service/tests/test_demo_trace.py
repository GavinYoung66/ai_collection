"""qc-service 演示时间线 GET /api/v1/qc/demo/{session_id}/trace，以及 CORS。"""
from __future__ import annotations

from uuid import UUID

from app.repositories.demo_store import TRACE_KEY_PREFIX, TRACE_TTL_SECONDS, trace_events_key
from app.repositories.job_store import QcJobStore
from app.services.demo_trace import CALL_QC_NOT_INVOKED, CALL_QC_STATUS_FIELD
from tests.test_demo_seed import FakeDemoRepo, _cleanup, _client, _seed_qc_source
from tests.test_job_store import MemoryRedis
from tests.test_replay_api import ApiFakeSession, ENVELOPE_KEYS, FakeDp, FakeQualityRepo

_TRACE = "/api/v1/qc/demo/{session_id}/trace"
_PENDING = "/api/v1/qc/demo/pending"
_ORIGIN_8080 = "http://localhost:8080"


def _trace_client():
    session = ApiFakeSession()
    redis = MemoryRedis(db=3)
    demo_repo = FakeDemoRepo(session)
    quality = FakeQualityRepo(session, QcJobStore(redis))
    dp = FakeDp({})
    client = _client(session=session, redis=redis, demo_repo=demo_repo, quality=quality, dp=dp)
    return client, session, redis, demo_repo, quality, dp


def _categories(events: list[dict]) -> set[str]:
    return {str(event.get("category") or "") for event in events}


class TestDemoTrace:
    def teardown_method(self):
        _cleanup()

    def test_unknown_session_is_200_empty_events_not_a2005(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        monkeypatch.delenv("LANGSMITH_TRACE_URL_TEMPLATE", raising=False)
        client, *_rest = _trace_client()
        sid = "qc_demo_unknown_session"
        resp = client.get(_TRACE.format(session_id=sid))
        assert resp.status_code == 200
        body = resp.json()
        assert ENVELOPE_KEYS <= set(body)
        assert body["code"] == "0"
        assert body["code"] != "A2005"
        data = body["data"]
        assert data["events"] == []
        assert data.get("call_id") is None
        assert data.get("call_id") != sid
        assert data["langsmith_url"] in (None, "")
        assert data["mcp_call_qc"] == CALL_QC_STATUS_FIELD
        assert CALL_QC_NOT_INVOKED in str(data.get("mcp_call_qc_note") or "")

    def test_no_langsmith_key_returns_200_and_empty_url(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        monkeypatch.delenv("LANGSMITH_TRACE_URL_TEMPLATE", raising=False)
        client, _session, redis, demo_repo, _quality, _dp = _trace_client()
        seed = client.post("/api/v1/qc/demo/seed").json()["data"]
        sid = seed["session_ids"][0]
        resp = client.get(_TRACE.format(session_id=sid))
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        assert isinstance(data["events"], list)
        assert data["langsmith_url"] in (None, "")
        assert data.get("call_id") != sid
        assert data["mcp_call_qc"] == CALL_QC_STATUS_FIELD
        key = trace_events_key(sid)
        assert key.startswith(TRACE_KEY_PREFIX)
        assert redis.ttl.get(key) == TRACE_TTL_SECONDS

    def test_replay_then_trace_has_distinct_pipeline_steps(self, monkeypatch):
        monkeypatch.setenv("LANGSMITH_API_KEY", "")
        monkeypatch.delenv("LANGSMITH_TRACE_URL_TEMPLATE", raising=False)
        client, _session, redis, demo_repo, _quality, dp = _trace_client()
        seed = client.post("/api/v1/qc/demo/seed").json()["data"]
        target = seed["sessions"][0]
        sid = target["session_id"]
        dp.sessions[sid] = _seed_qc_source(demo_repo, target)
        replay = client.post(f"/api/v1/qc/replay/{sid}", json={})
        assert replay.status_code == 200
        assert replay.json()["code"] in {"0", "A2011"}
        resp = client.get(_TRACE.format(session_id=sid))
        assert resp.status_code == 200
        body = resp.json()
        assert body["code"] == "0"
        data = body["data"]
        events = data["events"]
        assert isinstance(events, list)
        cats = _categories(events)
        assert {"dp", "ac", "rules", "llm", "search", "score"} <= cats
        names = {str(event.get("name") or "") for event in events}
        assert "qc-source" in names
        assert "ac_filter" in names
        assert "rules_r01_r15" in names
        assert "llm_review" in names
        assert "kb_search" in names
        assert "scoring" in names
        assert data["langsmith_url"] in (None, "")
        assert data.get("call_id") == target["call_id"]
        assert data.get("call_id") != sid
        UUID(str(data["call_id"]))
        note = " ".join(
            [
                str(data.get("mcp_call_qc_note") or ""),
                str(data.get("mcp_call_qc") or ""),
                *[str(event.get("summary") or "") for event in events],
                *[str(event.get("name") or "") for event in events],
            ]
        )
        assert "call_qc" in note
        assert "未调用" in note or data["mcp_call_qc"] == CALL_QC_STATUS_FIELD
        blob = " ".join(str(event.get("summary") or "") for event in events)
        assert "110101" not in blob
        assert redis.ttl.get(trace_events_key(sid)) == TRACE_TTL_SECONDS


class TestDemoCors:
    def teardown_method(self):
        _cleanup()

    def test_origin_8080_allowed_on_pending(self):
        client, *_rest = _trace_client()
        resp = client.get(_PENDING, headers={"Origin": _ORIGIN_8080})
        assert resp.status_code == 200
        assert resp.json()["code"] == "0"
        assert resp.headers.get("access-control-allow-origin") == _ORIGIN_8080

    def test_origin_8080_preflight_allowed(self):
        client, *_rest = _trace_client()
        resp = client.options(
            _PENDING,
            headers={
                "Origin": _ORIGIN_8080,
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code in {200, 204}
        assert resp.headers.get("access-control-allow-origin") == _ORIGIN_8080

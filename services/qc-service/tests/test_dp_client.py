"""QC-3.2 / QC-3.3：DP qc-source 映射与 pending-qc 列表/熔断。"""
from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import httpx
import pytest

from app.core.config import qc_settings
from app.integrations.dp_client import DP_RETRY_ATTEMPTS, PENDING_BREAKER_FAILURES, DpClient
from app.integrations.qc_session import map_qc_source
from app.main import app

_FIXTURE_PATH = Path(__file__).parent / "fixtures" / "qc_source.json"
_APP_ROOT = Path(__file__).resolve().parents[1] / "app"


def _fixture() -> dict:
    return json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))


def _envelope(data: dict) -> dict:
    return {"code": "0", "message": "ok", "data": data, "trace_id": "trace-qc-source"}


class TestQcSourceMapping:
    def test_fixture_maps_section_461(self):
        mapped = map_qc_source(_fixture()).to_mapped_dict()
        assert "result" in mapped
        assert mapped["result"] is None
        assert "dial_history_24h" not in mapped
        assert mapped["case_snapshot"]["ptp_amount"] is None
        assert mapped["case_snapshot"]["standard_answer_hit"] is None
        assert mapped["session_id"] == "sess_fixture_001"
        assert mapped["agent_type"] == "ai"
        assert mapped["call_id"] == "22222222-3333-4444-5555-666666666666"
        assert mapped["turns"][0]["speaker"] == "agent"
        assert mapped["turns"][1]["speaker"] == "user"

    def test_envelope_unwrap_and_result_key_always_present(self):
        raw = _fixture()
        raw.pop("result")
        mapped = map_qc_source(_envelope(raw)).to_mapped_dict()
        assert "result" in mapped
        assert mapped["result"] is None

    def test_speaker_ai_and_human_map_to_agent(self):
        raw = _fixture()
        raw["turns"][0]["speaker"] = "ai"
        raw["turns"].append(
            {"turn_id": "t3", "speaker": "human", "text": "人工坐席", "turn_index": 3}
        )
        session = map_qc_source(raw)
        assert session.turns[0].speaker == "agent"
        assert session.turns[2].speaker == "agent"

    def test_m1_forces_ptp_null_keeps_standard_answer_hit(self):
        raw = _fixture()
        raw["case_snapshot"]["ptp_amount"] = 1999.5
        raw["case_snapshot"]["standard_answer_hit"] = {"hit": True}
        mapped = map_qc_source(raw).to_mapped_dict()
        assert mapped["case_snapshot"]["ptp_amount"] is None
        assert mapped["case_snapshot"]["standard_answer_hit"] == {"hit": True}

    def test_standard_answer_hit_string_survives_mapping(self):
        raw = _fixture()
        raw["case_snapshot"]["standard_answer_hit"] = "条款原文截断"
        mapped = map_qc_source(raw).to_mapped_dict()
        assert mapped["case_snapshot"]["standard_answer_hit"] == "条款原文截断"

    def test_dial_history_kept_only_when_count_present(self):
        raw = _fixture()
        raw["dial_history_24h"] = {
            "phone_masked": "138****0000",
            "dial_count_24h": 2,
            "window_start": "2026-09-08T10:00:00+08:00",
            "window_end": "2026-09-09T10:00:00+08:00",
        }
        mapped = map_qc_source(raw).to_mapped_dict()
        assert mapped["dial_history_24h"]["dial_count_24h"] == 2

        omitted = _fixture()
        omitted["dial_history_24h"] = {"phone_masked": "138****0000"}
        mapped_omitted = map_qc_source(omitted).to_mapped_dict()
        assert "dial_history_24h" not in mapped_omitted

    def test_client_does_not_query_cases(self):
        text = (_APP_ROOT / "integrations" / "dp_client.py").read_text(encoding="utf-8")
        mapper = (_APP_ROOT / "integrations" / "qc_session.py").read_text(encoding="utf-8")
        combined = text + "\n" + mapper
        assert "FROM cases" not in combined
        assert "join cases" not in combined.lower()
        assert "SELECT" not in text


class TestDpQcSourceClient:
    def test_timeout_default_is_5s(self):
        client = DpClient()
        assert client._timeout == 5
        assert client.qc_source_url("sess_x").endswith("/api/v1/data/sessions/sess_x/qc-source")
        assert "/api/v1" not in qc_settings.QC_DP_BASE

    def test_no_inbound_callback_surface(self):
        assert not hasattr(DpClient, "callback")
        assert not hasattr(DpClient, "webhook")
        paths = [getattr(route, "path", "") for route in app.router.routes]
        assert not any(path in {"/qc/check", "/api/v1/qc/check"} for path in paths)

    @pytest.mark.asyncio
    async def test_get_qc_source_maps_fixture(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert request.url.path == "/api/v1/data/sessions/sess_fixture_001/qc-source"
            return httpx.Response(200, json=_envelope(_fixture()))

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
        session = await client.get_qc_source("sess_fixture_001")
        mapped = session.to_mapped_dict()
        assert "result" in mapped
        assert mapped["result"] is None
        assert "dial_history_24h" not in mapped

    @pytest.mark.asyncio
    async def test_qc_source_retries_three_times(self):
        hits = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            hits["n"] += 1
            raise httpx.ConnectTimeout("qc-source timeout")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
        with pytest.raises(httpx.ConnectTimeout):
            await client.get_qc_source("sess_timeout")
        assert hits["n"] == DP_RETRY_ATTEMPTS


class TestPendingQc:
    def test_parse_session_ids_only(self):
        client = DpClient()
        ids = client.parse_pending_qc(
            _envelope({"etl_date": "2026-09-09", "session_ids": ["sess_001", "sess_002"]})
        )
        assert ids == ["sess_001", "sess_002"]

    @pytest.mark.asyncio
    async def test_complement_fetches_qc_source_per_id(self):
        seen = {"pending": 0, "source": []}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("sessions:pending-qc") or "sessions:pending-qc" in str(request.url):
                seen["pending"] += 1
                assert request.url.params.get("etl_date") == "2026-09-09"
                assert request.url.params.get("limit") == "100"
                return httpx.Response(
                    200,
                    json=_envelope({"etl_date": "2026-09-09", "session_ids": ["sess_a", "sess_b"]}),
                )
            seen["source"].append(path)
            payload = deepcopy(_fixture())
            payload["session_id"] = path.split("/")[-2]
            return httpx.Response(200, json=_envelope(payload))

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
        sessions = await client.complement_pending_qc(etl_date="2026-09-09", limit=100)
        assert [item.session_id for item in sessions] == ["sess_a", "sess_b"]
        assert seen["pending"] == 1
        assert len(seen["source"]) == 2
        assert all("qc-source" in path for path in seen["source"])

    @pytest.mark.asyncio
    async def test_pending_circuit_skips_complement(self):
        seen = {"pending": 0, "source": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            if "pending-qc" in str(request.url):
                seen["pending"] += 1
                raise httpx.ConnectTimeout("pending-qc timeout")
            seen["source"] += 1
            return httpx.Response(200, json=_envelope(_fixture()))

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
        for _ in range(PENDING_BREAKER_FAILURES):
            ids = await client.list_pending_session_ids(etl_date="2026-09-09")
            assert ids == []
        assert client.pending_circuit_open is True
        source_before = seen["source"]
        pending_before = seen["pending"]
        sessions = await client.complement_pending_qc(etl_date="2026-09-09")
        assert sessions == []
        assert seen["source"] == source_before
        assert seen["pending"] == pending_before
        assert not any("qc-source" in url for url in client.recorded_urls())

    @pytest.mark.asyncio
    async def test_complement_skips_failed_qc_source_not_as_success(self):
        seen = {"source": []}

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if "sessions:pending-qc" in str(request.url):
                return httpx.Response(
                    200,
                    json=_envelope(
                        {"etl_date": "2026-09-09", "session_ids": ["sess_ok", "sess_fail", "sess_later"]}
                    ),
                )
            seen["source"].append(path)
            if path.split("/")[-2] == "sess_fail":
                raise httpx.ConnectError("qc-source failed")
            payload = deepcopy(_fixture())
            payload["session_id"] = path.split("/")[-2]
            return httpx.Response(200, json=_envelope(payload))

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
        client = DpClient(base_url="http://data-platform:8001", retry_delay=0, client=http)
        sessions = await client.complement_pending_qc(etl_date="2026-09-09")
        assert [item.session_id for item in sessions] == ["sess_ok", "sess_later"]
        assert "sess_fail" not in [item.session_id for item in sessions]
        fail_hits = [path for path in seen["source"] if "sess_fail" in path]
        assert len(fail_hits) == DP_RETRY_ATTEMPTS

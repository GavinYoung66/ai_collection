"""QC_R14_RAG_ENABLED 管道检索：开关 true 调 /kb/search；失败回退且不 PARTIAL。"""
from __future__ import annotations

import json

import httpx
import pytest

from app.core.config import qc_settings
from app.integrations.rag_client import RagClient
from app.services.llm_reviewer import LLMReviewer
from app.services.offline_qc_service import OfflineQcService
from common.errors.codes import ErrorCode


AGENT_TURN = "再不还就起诉你，还要强制执行"


def _turns(*rows: tuple[str, str, int]) -> list[dict]:
    return [
        {"turn_id": f"t{index}", "speaker": speaker, "text": text, "turn_index": index}
        for speaker, text, index in rows
    ]


def _session(**kwargs) -> dict:
    base = {
        "session_id": "sess_kb_search",
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "turns": _turns(("agent", AGENT_TURN, 1)),
        "result": None,
    }
    base.update(kwargs)
    return base


async def _empty_llm(_messages):
    return "[]"


def _offline(rag: RagClient) -> OfflineQcService:
    return OfflineQcService(
        llm=LLMReviewer(invoke=_empty_llm, retry_delay=0.0),
        rag=rag,
    )


def _rag(handler) -> RagClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
    return RagClient(base_url="http://rag-service:8000", retry_delay=0, client=http)


class TestKbSearchPipeline:
    @pytest.mark.asyncio
    async def test_flag_true_posts_search_and_fills_snippet(self, monkeypatch):
        monkeypatch.setattr(qc_settings, "QC_R14_RAG_ENABLED", True)
        clause = "自律公约第59条：严禁虚构免息、全额减免等虚假协商政策。"

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "POST"
            assert str(request.url).endswith("/api/v1/kb/search")
            body = json.loads(request.content.decode("utf-8"))
            assert body["query"] == AGENT_TURN
            assert body["category"] == "script"
            assert body["top_k"] == 3
            assert body["answer"] is False
            assert "测试检索" not in body["query"]
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": {"results": [{"content": clause, "score": 0.72}]},
                },
            )

        rag = _rag(handler)
        result = await _offline(rag).run(_session())
        assert any("/api/v1/kb/search" in url for url in rag.recorded_urls())
        assert any(method == "POST" for method, _url in rag.recorded_requests())
        assert rag.last_search_payload is not None
        assert rag.last_search_payload["query"] == AGENT_TURN
        assert result.qc_status != "PARTIAL"
        assert result.code not in {ErrorCode.A2001.value, "A2012"}
        snippets = [hit.kb_snippet for hit in result.hits if hit.kb_snippet]
        assert snippets
        assert all(item.startswith("自律公约第59条") for item in snippets)
        assert result.compliance_score is not None

    @pytest.mark.asyncio
    async def test_flag_true_timeout_still_scores_not_partial(self, monkeypatch):
        monkeypatch.setattr(qc_settings, "QC_R14_RAG_ENABLED", True)

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("kb search timeout")

        rag = _rag(handler)
        result = await _offline(rag).run(_session())
        assert any(method == "POST" and "/kb/search" in url for method, url in rag.recorded_requests())
        assert result.qc_status != "PARTIAL"
        assert result.qc_status in {"DONE", "PENDING_REVIEW"}
        assert result.compliance_score is not None
        assert result.code in {"0", ErrorCode.A2011.value}
        assert any(hit.rule_id == "R14" for hit in result.hits)

    @pytest.mark.asyncio
    async def test_flag_false_zero_kb_search(self, monkeypatch):
        monkeypatch.setattr(qc_settings, "QC_R14_RAG_ENABLED", False)

        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("flag=false must not HTTP search")

        rag = _rag(handler)
        result = await _offline(rag).run(_session())
        assert all("/kb/search" not in url for url in rag.recorded_urls())
        assert rag.last_search_payload is None
        assert any(hit.rule_id == "R14" for hit in result.hits)
        assert result.qc_status != "PARTIAL"

"""hotfix：graph citation allowlist + LLM fail-fast + reliability delete。"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.llm.validate import LLMConfigError, validate_real_llm_config
from app.main import app
from app.models.state import ComplianceOpinion, JudgmentPackage
from app.service.evidence_refs import (
    extract_citations_from_graph_final,
    extract_doc_ids_from_hits,
)


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


def test_extract_doc_ids_from_hits():
    assert extract_doc_ids_from_hits(
        [{"doc_id": "REG-1"}, {"doc_id": "REG-1"}, {"id": "T-2"}]
    ) == ["REG-1", "T-2"]


def test_extract_citations_from_graph_final_uses_mcp_hits_and_package():
    final = {
        "rag_hit_doc_ids": ["MCP-1", "MCP-2"],
        "judgment_package": JudgmentPackage(
            response_draft="答辩" * 20,
            rag_source_ids=["MCP-1", "FAKE"],
            regulation_refs=["MCP-2"],
        ),
        "compliance_opinion": ComplianceOpinion(
            overall_compliance="violation",
            regulation_refs=["MCP-1"],
        ),
    }
    allowed, regs, srcs = extract_citations_from_graph_final(final)
    assert "MCP-1" in allowed and "MCP-2" in allowed
    assert "FAKE" not in allowed  # 禁止 package 自报进白名单
    assert "MCP-1" in regs
    assert "MCP-1" in srcs
    assert "FAKE" in srcs  # 仍写入 rec；GET /verdict L4 按 allowed 剔除


def test_extract_citations_empty_final():
    allowed, regs, srcs = extract_citations_from_graph_final({})
    assert allowed == [] and regs == [] and srcs == []


def test_validate_real_llm_skips_when_mock(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "change_me")
    validate_real_llm_config()


def test_validate_real_llm_fails_without_keys(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_DASHSCOPE_API_KEY", "")
    monkeypatch.setattr(settings, "LLM_API_KEY", "change_me")
    monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "")
    with pytest.raises(LLMConfigError, match="USE_MOCK_LLM=false"):
        validate_real_llm_config()


def test_validate_real_llm_ok_with_deepseek(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "sk-real-key")
    validate_real_llm_config()


@pytest.mark.asyncio
async def test_llm_transport_error_writes_call_log_sink(tmp_path, monkeypatch):
    from app.llm.provider import ComplaintLLMProvider
    from app.observability.call_log_sink import TempFileCallLogSink, set_call_log_sink

    class Boom:
        async def invoke(self, **kwargs):
            raise RuntimeError("boom")

    path = tmp_path / "fail.jsonl"
    set_call_log_sink(TempFileCallLogSink(path))
    try:
        p = ComplaintLLMProvider(transport=Boom())
        with pytest.raises(RuntimeError, match="boom"):
            await p.invoke("x", agent_role="verdict")
        text = path.read_text(encoding="utf-8")
        assert "transport_error" in text
        assert '"channel": "llm"' in text or '"channel":"llm"' in text
    finally:
        set_call_log_sink(None)


def test_verdict_l4_keeps_graph_mcp_hit_evidence_refs(client):
    """P0：graph 路径须用 rag_hit_doc_ids 作 allowlist，GET /verdict 不得清空 evidence_refs。"""
    from app.models.state import VerdictType
    from app.service.complaint_service import complaint_service
    from app.service.evidence_refs import extract_citations_from_graph_final

    cid = client.post(
        "/api/v1/complaints", json={"complaint_text": "催收违规投诉"}
    ).json()["data"]["complaint_id"]
    final = {
        "rag_hit_doc_ids": ["REG-HIT-1", "REG-HIT-2"],
        "judgment_package": JudgmentPackage(
            response_draft="答辩草稿" * 15,
            rag_source_ids=["REG-HIT-1", "HALLUCINATED"],
            regulation_refs=["REG-HIT-2"],
        ),
        "compliance_opinion": ComplianceOpinion(
            overall_compliance="violation",
            regulation_refs=["REG-HIT-1"],
        ),
    }
    allowed, regs, srcs = extract_citations_from_graph_final(final)
    rec = complaint_service.get_or_raise(cid)
    rec["verdict"] = VerdictType.agent_violation
    rec["confidence"] = 0.91
    rec["response_draft"] = "答辩草稿" * 15
    rec["allowed_rag_doc_ids"] = allowed
    rec["regulation_refs"] = regs
    rec["rag_source_ids"] = srcs
    rec["task_status"] = "completed"

    data = client.get(f"/api/v1/complaints/{cid}/verdict").json()["data"]
    assert "REG-HIT-1" in data["evidence_refs"]
    assert "REG-HIT-2" in data["evidence_refs"]
    assert "HALLUCINATED" not in data["evidence_refs"]
    assert "HALLUCINATED" not in data["rag_source_ids"]

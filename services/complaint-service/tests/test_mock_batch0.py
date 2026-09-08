"""批次0 Mock 最小单测。

覆盖：
1. 种子数据可加载（8 条，四值 verdict，State 快照字段）
2. Mock LLM fixture 可加载 + 返回可被 Pydantic Schema 校验
3. Mock MCP 14 tool 三模式 + 场景 fixture
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.mock.data.seed_complaints import SEED_COMPLAINTS
from tests.mock.data.seed_cases import SEED_CASES
from tests.mock.data.seed_asr_samples import SEED_ASR_SAMPLES
from tests.mock.llm.mock_provider import MockLLMProvider
from tests.mock.mcp.mock_client import MockMCPClient, ALL_TOOLS, SERVERS
from tests.mock.mcp.errors import MCPError, MCPTimeoutError

# Schema 用服务内 State（app.models.state），禁止依赖已删除的仓库根旁路路径
from app.models.state import (
    CaseEvidenceBundle,
    ReflectionReport,
    FactTimeline,
    ComplianceOpinion,
    UserDemandAnalysis,
    VerdictDraft,
    JudgmentPackage,
    VerdictType,
)


# ────────────── B.1 种子数据 ──────────────

class TestSeedData:
    def test_seed_complaints_count_and_verdicts(self):
        assert len(SEED_COMPLAINTS) >= 8
        verdicts = {c["expected_verdict"] for c in SEED_COMPLAINTS}
        assert verdicts == {
            "agent_violation",
            "user_misunderstanding",
            "compliant_but_poor_experience",
            "pending",
        }

    def test_seed_complaints_state_snapshot_fields(self):
        required = {
            "complaint_id", "user_id", "case_ids", "complaint_text",
            "submit_time", "source_channel", "expected_verdict",
        }
        for c in SEED_COMPLAINTS:
            assert required <= set(c.keys())

    def test_seed_cases_match_complaints(self):
        for c in SEED_COMPLAINTS:
            for cid in c["case_ids"]:
                assert cid in SEED_CASES

    def test_seed_asr_coverage(self):
        # 至少覆盖正常与残缺场景
        has_normal = any(not s[0]["is_corrupted"] for s in SEED_ASR_SAMPLES.values() if s)
        has_corrupted = any(s[0]["is_corrupted"] for s in SEED_ASR_SAMPLES.values() if s)
        assert has_normal and has_corrupted


# ────────────── B.2 Mock LLM ──────────────

class TestMockLLMProvider:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "schema,expected_type",
        [
            (CaseEvidenceBundle, CaseEvidenceBundle),
            (ReflectionReport, ReflectionReport),
            (FactTimeline, FactTimeline),
            (ComplianceOpinion, ComplianceOpinion),
            (UserDemandAnalysis, UserDemandAnalysis),
            (VerdictDraft, VerdictDraft),
            (JudgmentPackage, JudgmentPackage),
        ],
    )
    async def test_invoke_returns_pydantic_instance(self, schema, expected_type):
        p = MockLLMProvider(seed=42)
        result = await p.invoke("test prompt", schema, "t1", "s1")
        assert isinstance(result, expected_type)

    @pytest.mark.asyncio
    async def test_verdict_fixtures_only_four_values(self):
        p = MockLLMProvider(seed=42)
        seen = set()
        for _ in range(20):
            r = await p.invoke("综合判责", VerdictDraft, "t1", "s1")
            seen.add(r.verdict.value)
        # 四值枚举，无 normal
        assert seen <= {v.value for v in VerdictType}
        assert "normal" not in seen

    @pytest.mark.asyncio
    async def test_error_mode_timeout(self):
        p = MockLLMProvider()
        p.set_error_mode("timeout")
        with pytest.raises(Exception):
            await p.invoke("x", VerdictDraft, "t1", "s1")

    @pytest.mark.asyncio
    async def test_seed_deterministic(self):
        p1 = MockLLMProvider(seed=123)
        p2 = MockLLMProvider(seed=123)
        r1 = await p1.invoke("综合判责", VerdictDraft, "t1", "s1")
        r2 = await p2.invoke("综合判责", VerdictDraft, "t1", "s1")
        assert r1.verdict == r2.verdict

    def test_all_18_fixtures_exist(self):
        fdir = Path(__file__).resolve().parent / "mock" / "llm" / "fixtures"
        expected = 18
        assert len(list(fdir.glob("*.json"))) == expected


# ────────────── B.3 Mock MCP ──────────────

class TestMockMCPClient:
    def test_14_tools_all_have_fixtures(self):
        fdir = Path(__file__).resolve().parent / "mock" / "mcp" / "fixtures"
        fixture_tools = set()
        for f in fdir.glob("*.json"):
            name = f.stem
            for server, tools in SERVERS.items():
                for tool in tools:
                    prefix = f"{server}_{tool}"
                    if name == prefix or name.startswith(prefix + "_"):
                        fixture_tools.add(tool)
        assert ALL_TOOLS <= fixture_tools

    @pytest.mark.asyncio
    async def test_success_mode(self):
        c = MockMCPClient()
        r = await c.call("business_ticket", "get_complaint_ticket",
                         {"complaint_id": "CP-2026-0001", "session_id": "s1"})
        assert r["complaint_id"] == "CP-2026-0001"

    @pytest.mark.asyncio
    async def test_timeout_mode(self):
        c = MockMCPClient()
        c.set_mode("query_call_asr", "timeout")
        with pytest.raises(MCPTimeoutError):
            await c.call("call_qc", "query_call_asr", {"case_id": "X", "session_id": "s1"})

    @pytest.mark.asyncio
    async def test_error_mode(self):
        c = MockMCPClient()
        c.set_mode("search_regulations", "error")
        with pytest.raises(MCPError):
            await c.call("rag_kb", "search_regulations", {"query": "x", "session_id": "s1"})

    @pytest.mark.asyncio
    async def test_scenario_fixtures(self):
        c = MockMCPClient()
        # multi_case
        r = await c.call("business_ticket", "query_case_info",
                         {"case_id": "C-2026-0004-A", "case_ids": ["a", "b", "c"], "session_id": "s1"})
        assert r["case_id"] == "C-2026-0004-A"
        # repeat
        r = await c.call("business_ticket", "query_user_history_complaints",
                         {"user_id": "U10005", "session_id": "s1"})
        assert r[0]["verdict"] == "agent_violation"
        # corrupted（显式 asr_corrupted；默认 ASR 走 normal）
        r = await c.call(
            "call_qc",
            "query_call_asr",
            {"case_id": "C-2026-0006", "session_id": "s1", "asr_corrupted": True},
        )
        assert r[0]["asr_text_with_timestamp"][0]["text"].startswith("###")
        # rag_match
        r = await c.call("rag_kb", "search_regulations", {"query": "x", "session_id": "s1"})
        assert r[0]["doc_id"] == "REG-001"

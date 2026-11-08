"""T113：complaint 6 个 LLM 节点中 5 个 prompt 未脱敏 → Provider 入口单点收口。

spec platform/observability「Prompt 外发前脱敏」：
- 投诉自述中的 PII 不落第三方：含手机号/证件号的 complaint_text 外发 payload 无明文；
- 所有 LLM 节点经同一入口：6 节点外发 payload 均经 ComplaintLLMProvider.invoke 脱敏。

现场核验（2026-11-07）：整改前已脱敏节点为 generate_package（节点侧逐变量
mask_before_persist），fact/compliance/verdict/reflection/user_demand 未脱敏；
实施删除节点侧重复实现，统一由 Provider 入口承接。
分层澄清：T98（审计落库脱敏：_call_log preview 经 mask_before_persist）与本项
（LLM 外发 payload 脱敏）分层不冲突，T98 既有形态保持不破坏。
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.llm.provider import ComplaintLLMProvider
from app.models.state import (
    ComplianceOpinion,
    ComplaintSessionState,
    FactTimeline,
    UserDemandAnalysis,
    VerdictDraft,
    VerdictType,
)

# 外发 PII 样本：手机号 13812345678 → 138****5678；身份证 110101199001011234 →
# 110101********1234（掩码形态见 common.security.mask）
_PHONE = "13812345678"
_PHONE_MASKED = "138****5678"
_ID_CARD = "110101199001011234"
_ID_MASKED = "110101********1234"
_COMPLAINT_TEXT = f"我是张三，手机号{_PHONE}，身份证{_ID_CARD}，商家拒不退款，要求处理。"

_APP_DIR = Path(__file__).resolve().parents[2] / "app"


def _no_plaintext(prompt: str) -> None:
    assert _PHONE not in prompt
    assert _ID_CARD not in prompt


def _pii_free(prompt: str) -> None:
    """强断言：明文不存在且掩码形态存在（适用于 prompt 确含 PII 的场景）。"""
    _no_plaintext(prompt)
    assert _PHONE_MASKED in prompt
    assert _ID_MASKED in prompt


class _CapturingTransport:
    """捕获外发 prompt 并返回固定合法 JSON（不经真实 LLM）。"""

    def __init__(self, payload: dict):
        self._payload = json.dumps(payload, ensure_ascii=False)
        self.prompts: list[str] = []

    async def invoke(self, *, prompt, **_kwargs):
        self.prompts.append(prompt)
        return self._payload


def _provider(payload: dict) -> tuple[ComplaintLLMProvider, _CapturingTransport]:
    transport = _CapturingTransport(payload)
    return ComplaintLLMProvider(transport=transport), transport


def _state(**kw) -> ComplaintSessionState:
    base = dict(session_id="s1", trace_id="t1", complaint_id="cmp1", complaint_text=_COMPLAINT_TEXT)
    base.update(kw)
    return ComplaintSessionState(**base)


# 6 个 LLM 节点 → (节点名, 节点前置 state kwargs, schema 最小合法外发载荷)
_NODE_CASES = [
    ("fact", {}, {"timeline": [], "summary": "s"}),
    ("compliance", {}, {"overall_compliance": "compliant"}),
    (
        "verdict",
        dict(
            fact_timeline=FactTimeline(summary="s"),
            compliance_opinion=ComplianceOpinion(overall_compliance="compliant"),
            user_demand=UserDemandAnalysis(
                demand_type="refund", emotion_level="calm", secondary_complaint_risk="low"
            ),
        ),
        {"verdict": "pending", "confidence": 0.8},
    ),
    (
        "reflection",
        {},
        {
            "evidence_sufficient": True,
            "missing_evidence": [],
            "contradictions": [],
            "suggestion": "ok",
        },
    ),
    (
        "user_demand",
        {},
        {"demand_type": "refund", "emotion_level": "calm", "secondary_complaint_risk": "low"},
    ),
    (
        "generate_package",
        dict(verdict_draft=VerdictDraft(verdict=VerdictType.pending, confidence=0.8)),
        {"evidence_chain": [], "response_draft": "x", "judgment_report": "x"},
    ),
]


class TestProviderEntryMasking:
    """外发 payload 无明文（spec 场景：投诉自述中的 PII 不落第三方）。"""

    @pytest.mark.asyncio
    async def test_invoke_masks_pii_before_outbound_payload(self):
        p, transport = _provider({"verdict": "pending", "confidence": 0.8})
        await p.invoke(f"投诉内容：{_COMPLAINT_TEXT}", trace_id="t1", session_id="s1")
        assert transport.prompts, "transport 未被调用"
        _pii_free(transport.prompts[0])

    @pytest.mark.asyncio
    async def test_mask_idempotent_for_already_masked_prompt(self):
        """已脱敏 prompt 再次经入口不二次变形（mask 幂等）。"""
        p, transport = _provider({"verdict": "pending", "confidence": 0.8})
        masked = f"投诉内容：手机号{_PHONE_MASKED}已脱敏"
        await p.invoke(masked, trace_id="t1", session_id="s1")
        assert transport.prompts[0] == masked

    @pytest.mark.asyncio
    async def test_audit_preview_still_masked_t98_form_kept(self):
        """T98 既有形态不破坏：_call_log 的 prompt_preview 仍经脱敏（审计落库分层）。"""
        p, _ = _provider({"verdict": "pending", "confidence": 0.8})
        await p.invoke(f"投诉内容：{_COMPLAINT_TEXT}", trace_id="t1", session_id="s1")
        entry = p.call_log[-1]
        assert _PHONE not in entry["prompt_preview"]
        assert _ID_CARD not in entry["prompt_preview"]


class TestAllSixNodesThroughSingleEntry:
    """spec 场景：所有 LLM 节点经同一脱敏入口（6 节点参数化）。"""

    @pytest.mark.parametrize(("node", "state_kw", "payload"), _NODE_CASES)
    def test_node_outbound_payload_has_no_plaintext_pii(self, node, state_kw, payload):
        import importlib

        mod = importlib.import_module(f"app.graph.nodes.{node}")
        p, transport = _provider(payload)
        mcp = AsyncMock()
        mcp.call = AsyncMock(return_value=[])
        with patch.object(mod, "get_llm_provider", return_value=p), patch.object(
            mod, "get_mcp_client", return_value=mcp, create=True
        ):
            out = asyncio.run(getattr(mod, f"{node}_node")(_state(**state_kw)))
        assert out.get("exception_info") is None, f"{node} 走了降级路径：{out}"
        assert transport.prompts, f"{node} 未发起 LLM 调用"
        for outbound in transport.prompts:
            # 外发 payload 无明文 PII。现场事实（2026-11-07）：6 个 complaint 模板
            # sections 均无 {{ }} 渲染占位符，节点传入的 complaint_text/evidence_text
            # 当前不进入外发 prompt（LLM 收不到原文属功能缺陷，修复超出 T113 范围，
            # 已在任务状态行留痕）；本断言在模板未来补占位符后由入口脱敏兜底掩码。
            _no_plaintext(outbound)

    def test_all_six_nodes_call_provider_invoke(self):
        """形态守护：6 节点均经同一 Provider 入口（get_llm_provider().invoke）。"""
        for node, _, _ in _NODE_CASES:
            src = (_APP_DIR / "graph" / "nodes" / f"{node}.py").read_text(
                encoding="utf-8"
            )
            assert "get_llm_provider" in src, f"{node} 未走 Provider 入口"
            assert "await llm.invoke(" in src, f"{node} 未调用 llm.invoke"


class TestNodeSideDuplicateRemoved:
    """节点侧删除重复实现而非新增（形态守护）。"""

    def test_generate_package_node_has_no_duplicate_masking(self):
        src = (_APP_DIR / "graph" / "nodes" / "generate_package.py").read_text(
            encoding="utf-8"
        )
        assert "mask_before_persist" not in src

    def test_provider_entry_is_single_mask_point(self):
        """脱敏收口在 Provider 入口：invoke 内调用 mask_before_persist。"""
        src = (_APP_DIR / "llm" / "provider.py").read_text(encoding="utf-8")
        assert "prompt = mask_before_persist(prompt)" in src
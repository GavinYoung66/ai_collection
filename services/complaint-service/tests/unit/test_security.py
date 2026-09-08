"""模块10 安全护栏测试。

覆盖：MCP 二次脱敏路径 / mask_before_persist / RBAC / L4（含 normal construct + AC）。
依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from app.mcp.client import MCPClient
from app.models.state import VerdictDraft, VerdictType
from app.service.audit_service import access_plaintext, can_access_plaintext
from app.service.guardrails_postcheck import postcheck
from app.service.security import has_plaintext_pii, mask_before_persist, prepare_agent_log


# ── 10.2 落库前脱敏 ──

def test_mask_before_persist_id_card():
    out = mask_before_persist("身份证110101199001011234")
    assert "110101199001011234" not in out


def test_mask_before_persist_phone():
    out = mask_before_persist("手机号13800138000")
    assert "13800138000" not in out


def test_has_plaintext_pii():
    assert has_plaintext_pii("13800138000") is True
    assert has_plaintext_pii("正常文本") is False


def test_prepare_agent_log_masks():
    log = prepare_agent_log("n", "手机13800138000", "身份证110101199001011234")
    assert "13800138000" not in log["input_summary"]
    assert "110101199001011234" not in log["output_summary"]


# ── 10.3 RBAC ──

def test_rbac_developer_denied():
    assert can_access_plaintext("developer") is False


def test_rbac_operator_final_granted():
    assert can_access_plaintext("operator_final") is True


def test_rbac_admin_dual_approval():
    assert can_access_plaintext("admin", dual_approved=False) is False
    assert can_access_plaintext("admin", dual_approved=True) is True


def test_access_plaintext_audit():
    assert access_plaintext("operator_final", "c:1", trace_id="t") is True
    assert access_plaintext("developer", "c:1", trace_id="t") is False


# ── 10.4 L4 后置校验 ──

def test_postcheck_legal_verdict_passes():
    pkg = {
        "verdict": "agent_violation",
        "confidence": 0.9,
        "response_draft": "坐席存在不当话术",
        "rag_source_ids": ["doc1"],
        "regulation_refs": ["doc1"],
    }
    ok, san = postcheck(pkg, rag_doc_ids=["doc1"])
    assert ok is True
    assert san["verdict"] == "agent_violation"
    assert san["stop_call_flag"] is True


def test_postcheck_enum_verdict_normalized():
    """Enum 成员不得因 str(Enum) 格式误判为非法。"""
    pkg = {
        "verdict": VerdictType.agent_violation,
        "confidence": 0.9,
        "response_draft": "坐席存在不当话术",
        "rag_source_ids": ["doc1"],
        "regulation_refs": ["doc1"],
    }
    ok, san = postcheck(pkg, rag_doc_ids=["doc1"])
    assert ok is True
    assert san["verdict"] == "agent_violation"


def test_postcheck_rejects_normal_via_model_construct():
    """用 model_construct 绕过 Pydantic 枚举校验，normal 必须被 L4 拦截。"""
    draft = VerdictDraft.model_construct(
        verdict="normal",
        confidence=0.9,
        reasoning="x",
        response_draft="x",
        regulation_refs=[],
        rag_source_ids=[],
    )
    pkg = draft.model_dump() if hasattr(draft, "model_dump") else dict(draft)
    # model_dump 可能仍失败；强制 dict
    pkg = {
        "verdict": getattr(draft, "verdict", "normal"),
        "confidence": 0.9,
        "response_draft": "x",
        "rag_source_ids": [],
        "regulation_refs": [],
    }
    ok, san = postcheck(pkg, rag_doc_ids=[])
    assert ok is False
    assert san["verdict"] == "pending"


def test_postcheck_blocks_fake_rag_source():
    pkg = {
        "verdict": "user_misunderstanding",
        "confidence": 0.8,
        "response_draft": "用户误解",
        "rag_source_ids": ["fake_doc"],
        "regulation_refs": ["fake_doc"],
    }
    ok, san = postcheck(pkg, rag_doc_ids=["real_doc"])
    # 编造来源 → 剔除非法 id，保留正文（不再整包兜底）
    assert ok is True
    assert san["rag_source_ids"] == []
    assert san["regulation_refs"] == []
    assert san["response_draft"] == "用户误解"


def test_postcheck_rejects_self_proving_sources():
    """空 allowed 时剔除全部引用，但不应因假 source 整包失败。"""
    pkg = {
        "verdict": "agent_violation",
        "confidence": 0.9,
        "response_draft": "ok",
        "rag_source_ids": ["fake"],
        "regulation_refs": ["fake"],
    }
    ok, san = postcheck(pkg, rag_doc_ids=[])
    assert ok is True
    assert san["rag_source_ids"] == []
    assert san["regulation_refs"] == []


def test_postcheck_hallucination_amount_fallback():
    pkg = {
        "verdict": "agent_violation",
        "confidence": 0.9,
        "response_draft": "需赔偿500元，3天内处理",
        "rag_source_ids": ["d1"],
        "regulation_refs": [],
    }
    ok, san = postcheck(pkg, rag_doc_ids=["d1"], rag_text="无金额信息")
    assert ok is True
    assert "坐席核实" in san["response_draft"]


def test_postcheck_ac_redline_blocks():
    pkg = {
        "verdict": "agent_violation",
        "confidence": 0.9,
        "response_draft": "我们是法院的，再不还钱就上门打人",
        "rag_source_ids": ["d1"],
        "regulation_refs": [],
    }
    ok, san = postcheck(pkg, rag_doc_ids=["d1"])
    assert ok is True
    assert "坐席核实" in san["response_draft"]
    assert "法院" not in san["response_draft"]
    assert "打人" not in san["response_draft"]


def test_postcheck_retry_then_rule_based():
    calls = {"n": 0}

    def retry_fn():
        calls["n"] += 1
        return {
            "verdict": "normal",
            "confidence": 0.5,
            "response_draft": "x",
            "rag_source_ids": [],
            "regulation_refs": [],
        }

    ok, san = postcheck(
        {"verdict": "normal", "confidence": 0.5, "response_draft": "x",
         "rag_source_ids": [], "regulation_refs": []},
        rag_doc_ids=[],
        max_retries=3,
        retry_fn=retry_fn,
    )
    assert ok is False
    assert calls["n"] == 3
    assert san["verdict"] == "pending"


# ── 10.1 MCP 二次脱敏（走 Client 真实路径） ──

class _PiiTransport:
    async def call(self, server, tool, params, **kw):
        return {"phone": "13800138000", "content": "身份证110101199001011234"}


def test_mcp_client_masks_pii():
    client = MCPClient(transport=_PiiTransport())
    with patch("app.mcp.client.log_action") as mock_audit:
        result = asyncio.run(client.call("rag", "search", {"q": "x"}, trace_id="t"))
        assert "13800138000" not in str(result)
        assert "110101199001011234" not in str(result)
        assert client.call_log[-1]["status"] == "success"
        mock_audit.assert_called()
        assert mock_audit.call_args.kwargs.get("action") == "mcp_pii_leak_sanitized" or (
            mock_audit.call_args.args and "mcp_pii_leak_sanitized" in str(mock_audit.call_args)
        )

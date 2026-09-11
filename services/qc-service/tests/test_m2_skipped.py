"""QC-11.4：M2+ 能力明确 skip，禁止为跳过而去实现。"""
from __future__ import annotations

import pytest


@pytest.mark.skip(reason="M2+: 不测 RAG 标准答案检索 POST /api/v1/kb/search；M1 QC_R14_RAG_ENABLED=false")
def test_skip_rag_standard_answer_kb_search():
    raise AssertionError("M1 must not execute /kb/search")


@pytest.mark.skip(reason="M2+: 不测红线 6；不测 audit_logs.action=guardrail.pass；红线 6 非 M1 必过")
def test_skip_redline6_guardrail_pass():
    raise AssertionError("M1 must not assert guardrail.pass")


@pytest.mark.skip(reason="M1 不测实时 WS /qc/check/stream")
def test_skip_realtime_ws_qc_check_stream():
    raise AssertionError("M1 must not require WS /qc/check/stream")

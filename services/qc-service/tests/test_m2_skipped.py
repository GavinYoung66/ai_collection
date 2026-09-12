"""M2+ 能力：红线 6 / WS 仍 skip；kb/search 已由本 change 解禁。"""
from __future__ import annotations

import pytest

from app.integrations.rag_client import RagClient


def test_kb_search_client_covered_elsewhere():
    """RagClient.search 与管道调用见 test_rag_client.py / test_kb_search.py。"""
    assert hasattr(RagClient, "search")


@pytest.mark.skip(reason="M2+: 不测红线 6；不测 audit_logs.action=guardrail.pass；红线 6 非 M1 必过")
def test_skip_redline6_guardrail_pass():
    raise AssertionError("M1 must not assert guardrail.pass")


@pytest.mark.skip(reason="M1 不测实时 WS /qc/check/stream")
def test_skip_realtime_ws_qc_check_stream():
    raise AssertionError("M1 must not require WS /qc/check/stream")

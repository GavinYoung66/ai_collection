"""5.1 金标：坐席违规 / 客户侧异常 / 干净合规三类正文。"""
from __future__ import annotations

from common.complaint_text import (
    CATEGORY_AGENT,
    CATEGORY_COMPLIANT,
    CATEGORY_USER,
    build_complaint_text,
)


def test_golden_agent_violation():
    violations = [
        {"speaker": "agent", "redline_tag": "threat", "rule_id": "R01"},
        {"speaker": "agent", "redline_tag": "privacy_leak", "rule_id": "R08"},
    ]
    cat, text = build_complaint_text(
        violations=violations, customer="张*", overdue_amount="12000"
    )
    assert cat == CATEGORY_AGENT
    assert "坐席" in text
    assert "威胁恐吓" in text
    assert "隐私泄露" in text
    assert "张*" in text
    assert "12000" in text


def test_golden_user_misunderstanding():
    violations = [
        {"speaker": "user", "redline_tag": "unverified", "rule_id": "U01"},
    ]
    cat, text = build_complaint_text(
        violations=violations, customer="李*", overdue_amount="500"
    )
    assert cat == CATEGORY_USER
    assert "客户侧" in text or "客户" in text
    assert "未核实信息" in text
    assert "坐席通话中存在违规" not in text


def test_golden_compliant_no_violations():
    cat, text = build_complaint_text(
        violations=[], customer="王*", overdue_amount="0"
    )
    assert cat == CATEGORY_COMPLIANT
    assert "未发现违规" in text or "合规" in text
    assert "威胁恐吓" not in text


def test_agent_takes_precedence_over_user():
    violations = [
        {"speaker": "user", "redline_tag": "unverified", "rule_id": "U01"},
        {"speaker": "agent", "redline_tag": "threat", "rule_id": "R01"},
    ]
    cat, text = build_complaint_text(violations=violations)
    assert cat == CATEGORY_AGENT
    assert "威胁恐吓" in text


def test_different_cases_different_texts():
    """两起投诉正文应反映各自违规项（非千人一面）。"""
    _, t1 = build_complaint_text(
        violations=[{"speaker": "agent", "redline_tag": "threat", "rule_id": "R01"}],
        customer="A",
        overdue_amount="1",
    )
    _, t2 = build_complaint_text(
        violations=[{"speaker": "agent", "redline_tag": "fake_identity", "rule_id": "R02"}],
        customer="B",
        overdue_amount="2",
    )
    assert t1 != t2
    assert "威胁恐吓" in t1 and "冒充身份" in t2

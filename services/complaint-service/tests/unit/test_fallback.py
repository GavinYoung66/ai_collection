"""规则兜底引擎单元测试：6 个 generate 方法 + [规则兜底] 前缀 + verdict 四值。"""
from __future__ import annotations

from app.llm.fallback import ComplaintRuleEngine, VERDICT_VALID


def test_generate_reflection_report():
    e = ComplaintRuleEngine()
    r = e.generate_reflection_report(evidence_items=[{"a": 1}, {"b": 2}, {"c": 3}])
    assert "[规则兜底]" in r["summary"]
    assert r["sufficient"] is True


def test_generate_verdict_draft_with_violation():
    e = ComplaintRuleEngine()
    r = e.generate_verdict_draft(qc_violations=[{"rule": "x"}])
    assert r["verdict"] in VERDICT_VALID
    assert r["verdict"] == "agent_violation"
    assert r["confidence"] == 0.5
    assert "[规则兜底]" in r["reasoning"]


def test_generate_verdict_draft_no_violation():
    e = ComplaintRuleEngine()
    r = e.generate_verdict_draft(qc_violations=[])
    assert r["verdict"] in VERDICT_VALID
    assert r["verdict"] != "normal"


def test_generate_judgment_package():
    e = ComplaintRuleEngine()
    r = e.generate_judgment_package()
    assert "[规则兜底]" in r["response_draft"]
    assert r["requires_human_review"] is True


def test_generate_compliance_opinion():
    e = ComplaintRuleEngine()
    r = e.generate_compliance_opinion()
    assert "[规则兜底]" in r["compliance_conclusion"]
    assert r["regulation_refs"] == []


def test_generate_user_demand():
    e = ComplaintRuleEngine()
    r = e.generate_user_demand(text="我要退款")
    assert r["demand_type"] == "refund"
    assert "[规则兜底]" in r["summary"]


def test_generate_fact_timeline():
    e = ComplaintRuleEngine()
    events = [{"timestamp": "2024-01-02"}, {"timestamp": "2024-01-01"}]
    r = e.generate_fact_timeline(events=events)
    assert r["events"][0]["timestamp"] == "2024-01-01"
    assert "[规则兜底]" in r["summary"]

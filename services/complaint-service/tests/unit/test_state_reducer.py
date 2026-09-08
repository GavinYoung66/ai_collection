"""merge_case_evidence reducer 单元测试（字段级覆盖语义）。"""
from __future__ import annotations

from app.models.state import CaseEvidenceBundle, merge_case_evidence


def test_same_case_id_field_overwrite():
    """同 case_id：右侧字段有值则整字段覆盖，为空则保留左侧。"""
    left = {"c1": CaseEvidenceBundle(case_info={"a": 1}, call_evidence=[{"id": "A"}])}
    right = {"c1": CaseEvidenceBundle(call_evidence=[{"id": "B"}])}
    merged = merge_case_evidence(left, right)
    assert merged["c1"].case_info == {"a": 1}  # 右为空，保留左
    assert merged["c1"].call_evidence == [{"id": "B"}]  # 右有值，整字段覆盖


def test_different_case_ids_preserved():
    """不同 case_id 各自保留。"""
    left = {"c1": CaseEvidenceBundle(case_info={"a": 1})}
    right = {"c2": CaseEvidenceBundle(case_info={"b": 2})}
    merged = merge_case_evidence(left, right)
    assert set(merged.keys()) == {"c1", "c2"}


def test_dict_input_compatible():
    """reducer 兼容 dict 输入（LangGraph 序列化后）。"""
    left = {"c1": {"case_id": "c1", "case_info": {"a": 1}}}
    right = {"c1": {"case_id": "c1", "call_evidence": [{"id": "e1"}]}}
    merged = merge_case_evidence(left, right)
    assert isinstance(merged["c1"], CaseEvidenceBundle)
    assert merged["c1"].case_info == {"a": 1}


def test_right_overrides_nonempty_field():
    """右侧非空字段覆盖左侧同名字段。"""
    left = {"c1": CaseEvidenceBundle(case_info={"old": 1}, history_tickets=[{"id": 1}])}
    right = {"c1": CaseEvidenceBundle(case_info={"new": 2})}
    merged = merge_case_evidence(left, right)
    assert merged["c1"].case_info == {"new": 2}
    assert merged["c1"].history_tickets == [{"id": 1}]  # 右为空，保留左

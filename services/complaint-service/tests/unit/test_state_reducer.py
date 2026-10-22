"""merge_case_evidence reducer 单元测试（字段级覆盖语义）。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.state import (
    CaseEvidenceBundle,
    ComplianceItem,
    ComplianceOpinion,
    FactTimeline,
    TimelineEvent,
    VerdictDraft,
    VerdictType,
    merge_case_evidence,
)


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


def test_compliance_verdict_accepts_all_real_values():
    """枚举并集：LLM 值域 + 降级值域全部可通过（防收紧后降级被打回）。"""
    for value in ("compliant", "partial_violation", "violation", "uncertain"):
        assert ComplianceOpinion(overall_compliance=value).overall_compliance == value


def test_compliance_verdict_rejects_out_of_domain_value():
    """值域外的取值被拦下（大小写不符也不行）。"""
    with pytest.raises(ValidationError):
        ComplianceOpinion(overall_compliance="Compliant")


def test_conflict_highlight_without_conflicts_auto_downgraded():
    """软校验：标记冲突但冲突列表为空 → 自动归一为 False，不抛异常。"""
    draft = VerdictDraft(verdict=VerdictType.pending, confidence=0.4, conflict_highlight=True)
    assert draft.conflict_highlight is False


def test_conflict_highlight_with_conflicts_kept():
    """确有冲突点 → 保留人工复核标记。"""
    draft = VerdictDraft(
        verdict=VerdictType.pending,
        confidence=0.4,
        conflicts=["通话记录与工单描述不一致"],
        conflict_highlight=True,
    )
    assert draft.conflict_highlight is True


def test_timeline_item_typed_and_attribute_accessible():
    """timeline 嵌套结构下沉为子模型：字典自动转模型、可按属性取值。"""
    ft = FactTimeline(
        timeline=[
            {"event_time": "2026-09-01T10:00:00", "source": "complaint", "content": "用户提交投诉"}
        ]
    )
    assert isinstance(ft.timeline[0], TimelineEvent)
    assert ft.timeline[0].event_time == "2026-09-01T10:00:00"
    assert ft.timeline[0].source == "complaint"


def test_timeline_item_rejects_wrong_field_type():
    """已知字段类型错误被拦下（此前 list[dict] 一律放行）。"""
    with pytest.raises(ValidationError):
        FactTimeline(timeline=[{"event_time": 12345}])


def test_timeline_item_tolerates_extra_field():
    """extra=allow：上游/LLM 追加字段不阻塞。"""
    ft = FactTimeline(
        timeline=[{"event_time": "t", "source": "call", "content": "c", "upstream_note": "x"}]
    )
    assert ft.timeline[0].event_time == "t"


def test_compliance_item_typed_and_attribute_accessible():
    """opinions 嵌套结构下沉为子模型。"""
    op = ComplianceOpinion(
        overall_compliance="violation",
        opinions=[
            {
                "behavior": "23:05致电用户",
                "is_violation": True,
                "regulation_refs": ["REG-001"],
                "reasoning": "违反催收时段规定",
            }
        ],
    )
    assert isinstance(op.opinions[0], ComplianceItem)
    assert op.opinions[0].is_violation is True
    assert op.opinions[0].regulation_refs == ["REG-001"]


def test_compliance_item_rejects_wrong_field_type():
    """已知字段类型错误被拦下。"""
    with pytest.raises(ValidationError):
        ComplianceOpinion(overall_compliance="compliant", opinions=[{"behavior": 999}])

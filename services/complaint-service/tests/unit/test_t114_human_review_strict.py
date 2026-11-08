"""T114：human_review 非法/缺失恢复值静默 approve → fail-safe 严格校验。

spec complaint-service/graph-nodes「人工审阅恢复的 fail-safe 判定」三场景：
- 非法恢复值（未知字符串/非法枚举/恶意构造）→ 保持待复核 + exception_info 记录非法值，
  不转 resolved（MUST NOT 触发工单自动结案）；
- 合法恢复值（approve/reject 等受支持动作）→ 按动作正常推进并保留审计记录；
- 缺失恢复值（None/空串/dict 无 review_action/不支持类型）→ 不默认 approve，
  保持待复核并告警。
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest


from app.graph.nodes import human_review as hr_mod
from app.models.state import ComplaintSessionState, ReviewAction, TaskStatus


def _state(**kw) -> ComplaintSessionState:
    base = dict(session_id="s1", trace_id="t1", complaint_id="cmp1", complaint_text="x")
    base.update(kw)
    return ComplaintSessionState(**base)


def _run(review_value, **state_kw):
    with patch.object(hr_mod, "interrupt", return_value=review_value):
        return asyncio.run(hr_mod.human_review_node(_state(**state_kw)))


class TestInvalidRecoveryValue:
    """spec 场景 1：非法恢复值不自动结案。"""

    @pytest.mark.parametrize(
        "bad",
        [
            "garbage",
            "APPROVE",  # 大小写敏感，非法枚举值
            "approve; DROP TABLE complaints",  # 恶意构造
            " approve",  # 带空白前缀非法
        ],
    )
    def test_unknown_string_keeps_pending_review_with_exception_info(self, bad):
        out = _run(bad)
        assert out["task_status"] == TaskStatus.pending_review.value
        assert out["task_status"] != TaskStatus.resolved.value
        assert out["exception_info"]["error_type"] == "invalid_review_action"
        assert repr(bad) in out["exception_info"]["message"]
        assert out["exception_info"]["node"] == "human_review"
        assert out["human_review"]["review_action"] is None

    def test_dict_with_unknown_action_keeps_pending_review(self):
        out = _run({"review_action": "garbage"})
        assert out["task_status"] == TaskStatus.pending_review.value
        assert out["exception_info"]["error_type"] == "invalid_review_action"
        assert "garbage" in out["exception_info"]["message"]

    def test_dict_with_malicious_payload_keeps_pending_review(self):
        out = _run({"review_action": {"$gt": ""}})
        assert out["task_status"] == TaskStatus.pending_review.value

    def test_dict_with_non_string_action_keeps_pending_review(self):
        out = _run({"review_action": 123})
        assert out["task_status"] == TaskStatus.pending_review.value

    def test_unsupported_type_keeps_pending_review(self):
        out = _run(123)
        assert out["task_status"] == TaskStatus.pending_review.value
        assert "unsupported" in out["exception_info"]["message"]

    def test_reject_count_not_advanced_on_invalid(self):
        out = _run("garbage", reject_count=2)
        # 不写顶层 reject_count 键 = LangGraph 部分更新保持原值（非法恢复值不推进计数）
        assert "reject_count" not in out
        assert out["human_review"]["reject_count"] == 2

    def test_warning_logged_on_invalid(self, monkeypatch):
        records: list[dict] = []

        class _CaptureLogger:
            def warning(self, event, **kw):
                records.append({"event": event, **kw})

            def __getattr__(self, name):
                return lambda *a, **k: None

        monkeypatch.setattr(hr_mod, "logger", _CaptureLogger())
        _run("garbage")
        assert any(
            r["event"] == "human_review_invalid_recovery_value"
            and r.get("detail")
            for r in records
        )


class TestLegalRecoveryValue:
    """spec 场景 2：合法恢复值正常推进并保留审计记录。"""

    @pytest.mark.parametrize(
        ("action", "status"),
        [
            ("approve", "resolved"),
            ("reject_rejudge", "evidence_gathering"),
            ("escalate", "pending_senior_review"),
            ("modify_verdict", "resolved"),
            ("modify_response", "resolved"),
            ("add_evidence", "evidence_gathering"),
        ],
    )
    def test_all_supported_actions_advance(self, action, status):
        out = _run(action)
        assert out["task_status"] == status
        assert out["human_review"]["review_action"] == action

    def test_review_action_enum_instance_accepted(self):
        out = _run(ReviewAction.approve)
        assert out["task_status"] == TaskStatus.resolved.value
        assert out["human_review"]["review_action"] == "approve"

    def test_dict_legal_action_advances(self):
        out = _run({"review_action": "approve"})
        assert out["task_status"] == TaskStatus.resolved.value
        assert out["human_review"]["review_action"] == "approve"

    def test_reject_rejudge_increments_count(self):
        out = _run("reject_rejudge", reject_count=1)
        assert out["task_status"] == "evidence_gathering"
        assert out["human_review"]["reject_count"] == 2
        assert out["reject_count"] == 2


class TestMissingRecoveryValue:
    """spec 场景 3：缺失恢复值保守处理，不默认 approve。"""

    @pytest.mark.parametrize(
        "missing",
        [None, "", {}, {"review_action": ""}, {"review_action": None}],
    )
    def test_missing_value_keeps_pending_review(self, missing):
        out = _run(missing)
        assert out["task_status"] == TaskStatus.pending_review.value
        assert out["task_status"] != TaskStatus.resolved.value
        assert out["exception_info"]["error_type"] == "invalid_review_action"

    def test_missing_value_logs_warning(self, monkeypatch):
        records: list[dict] = []

        class _CaptureLogger:
            def warning(self, event, **kw):
                records.append({"event": event, **kw})

            def __getattr__(self, name):
                return lambda *a, **k: None

        monkeypatch.setattr(hr_mod, "logger", _CaptureLogger())
        _run(None)
        assert any(
            r["event"] == "human_review_invalid_recovery_value" for r in records
        )
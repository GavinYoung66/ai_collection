"""人工复核服务（8.2a）。

- 6 动作：approve / modify_verdict / modify_response / reject_rejudge / add_evidence / escalate
- reject_count 硬上限 2（第3次强制 escalate）
- 驳回原因枚举；状态映射；写 agent_logs；联动 human_review_node（stub）
- 可注入 session_service（tasks 冒烟）；默认走 complaint_service 门面
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Union

from app.models.state import ReviewAction, TaskStatus, VerdictType
from app.service.security import prepare_agent_log

REJECT_MAX = 2

ActionLike = Union[ReviewAction, str]
ReasonLike = Union["RejectReason", str, None]
VerdictLike = Union[VerdictType, str, None]


class RejectReason(str, Enum):
    insufficient_evidence = "insufficient_evidence"
    wrong_verdict = "wrong_verdict"
    bad_response = "bad_response"
    other = "other"


_VALID_VERDICTS = {v.value for v in VerdictType}


def _norm_action(action: ActionLike) -> ReviewAction:
    if isinstance(action, ReviewAction):
        return action
    return ReviewAction(str(action))


def _norm_reason(reason: ReasonLike) -> Optional[RejectReason]:
    if reason is None:
        return None
    if isinstance(reason, RejectReason):
        return reason
    return RejectReason(str(reason))


def _norm_verdict(verdict: VerdictLike) -> Optional[VerdictType]:
    if verdict is None:
        return None
    if isinstance(verdict, VerdictType):
        return verdict
    v = str(verdict)
    if v not in _VALID_VERDICTS:
        raise ValueError(f"invalid verdict: {verdict}")
    return VerdictType(v)


class ReviewService:
    def __init__(self, session_service: Any = None) -> None:
        self._reject_count: dict[str, int] = {}
        self._session_service = session_service  # 注入时按 session_id 工作

    def _rec(self, complaint_id: str) -> dict:
        from app.service.complaint_service import complaint_service
        return complaint_service.get_or_raise(complaint_id)

    def get_status(self, id_: str) -> Optional[str]:
        """tasks 冒烟：session 模式下返回 session 状态。"""
        if self._session_service is not None:
            return self._session_service.get_status(id_)
        from app.service.complaint_service import complaint_service
        st = complaint_service.get_status(id_)["task_status"]
        return st.value if hasattr(st, "value") else str(st)

    def submit(self, id_: str, action: ActionLike,
               reviewer: str = "operator", comment: Optional[str] = None,
               reason: ReasonLike = None,
               new_verdict: VerdictLike = None) -> dict:
        action = _norm_action(action)
        reason = _norm_reason(reason)
        try:
            new_verdict = _norm_verdict(new_verdict)
        except ValueError:
            raise

        if action in (ReviewAction.reject_rejudge, ReviewAction.modify_verdict,
                      ReviewAction.modify_response) and reason is None:
            raise ValueError(f"action {action.value} requires reason")

        if new_verdict is not None and new_verdict.value not in _VALID_VERDICTS:
            raise ValueError(f"invalid verdict: {new_verdict}")

        # reject_count 上限：第3次 reject_rejudge 强制 escalate
        if action == ReviewAction.reject_rejudge:
            cnt = self._reject_count.get(id_, 0)
            if cnt >= REJECT_MAX:
                action = ReviewAction.escalate
            else:
                self._reject_count[id_] = cnt + 1

        if action == ReviewAction.approve:
            target = TaskStatus.resolved
        elif action in (ReviewAction.reject_rejudge, ReviewAction.add_evidence):
            target = TaskStatus.evidence_gathering
        elif action == ReviewAction.escalate:
            target = TaskStatus.pending_senior_review
        else:
            target = TaskStatus.judging

        def _apply(ss: Any, sid: str, tgt: TaskStatus) -> None:
            """确保可到达目标：先进入 pending_review（流水线），再单步到 resolved 等。"""
            cur = ss.get_status(sid)
            if tgt == TaskStatus.resolved and cur not in (
                TaskStatus.pending_review.value,
                TaskStatus.pending_senior_review.value,
                TaskStatus.resolved.value,
            ):
                if not ss.transition(sid, TaskStatus.pending_review):
                    raise RuntimeError(f"cannot advance to pending_review before {tgt.value}")
            ok = ss.transition(sid, tgt)
            if not ok:
                raise RuntimeError(f"illegal transition to {tgt.value}")

        if self._session_service is not None:
            _apply(self._session_service, id_, target)
            self._human_review_node_hook(id_, action, reviewer)
            return {"session_id": id_, "task_status": target, "review_action": action}

        # 门面模式：id_ 为 complaint_id
        from app.events.publisher import publisher
        from app.service.complaint_service import complaint_service
        from app.service.session_service import session_service as global_ss

        rec = self._rec(id_)
        sid = rec["session_id"]
        _apply(global_ss, sid, target)
        complaint_service._sync_from_session(rec)
        rec["review_action"] = action
        if new_verdict is not None:
            rec["verdict"] = new_verdict
            # 11.2 停止致电联动：非违规 verdict → 恢复可外呼
            v = new_verdict.value if hasattr(new_verdict, "value") else str(new_verdict)
            if v != VerdictType.agent_violation.value:
                rec["stop_call_flag"] = False
        # P1：旧 approve 关单前也须有回电契约（幂等标记 + 事件）
        if action == ReviewAction.approve and not rec.get("callback_scheduled"):
            est = rec.get("disposition_verdict") or rec.get("verdict")
            est_val = (
                est.value if hasattr(est, "value") else (str(est) if est is not None else "approved")
            )
            publisher.publish(
                "callback.requested",
                {
                    "complaint_id": id_,
                    "establishment": est_val,
                    "callback_required": True,
                    "note": "legacy_approve_auto_callback",
                    "operator": reviewer,
                    "session_id": sid,
                    "source": "review_approve",
                },
            )
            rec["callback_scheduled"] = True
        rec["agent_logs"].append(
            prepare_agent_log(
                "human_review",
                f"complaint_id={id_}",
                f"action={action.value} reason={reason.value if reason else None} reviewer={reviewer}",
                duration_ms=0,
            )
        )
        rec["updated_at"] = datetime.now(timezone.utc)
        self._human_review_node_hook(id_, action, reviewer)
        return rec

    @staticmethod
    def _human_review_node_hook(complaint_id: str, action: ReviewAction,
                                reviewer: str) -> None:
        """联动 human_review_node。本批 stub。"""
        pass


# 全局单例
review_service = ReviewService()

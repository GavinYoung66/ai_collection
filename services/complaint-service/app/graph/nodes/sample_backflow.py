"""sample_backflow 样本回流节点。

调用 MCP write_session + write_human_review_truth 回写样本。
重试只有单层：data_platform 桥内 3 次，用尽后数据入 csmp:backflow:retry
并返回成功形状（见 DataPlatformBridge）；节点不再重复重试，否则双层叠加
会变成每 tool 9 次 HTTP + 3 条重复 LPUSH（N5）。桥外传输异常仅记录告警，
不阻塞流程。

write_session 且 persisted=True 时发布 complaint.result.ready（5.5）。
"""
from __future__ import annotations

import structlog

from app.api.deps import get_mcp_client
from app.events.publisher import publisher
from app.mcp.errors import MCPError, MCPTimeoutError
from app.models.state import ComplaintSessionState, TaskStatus

logger = structlog.get_logger()

_SERVER = "data_platform"
# case_id 级防双发（同进程内；重复成功不刷屏）
_result_ready_published: set[str] = set()


def reset_result_ready_dedupe() -> None:
    """测试用。"""
    _result_ready_published.clear()


def _task_status_value(status: object) -> str:
    """快照列 task_status 只收枚举裸值（resolved 等），禁止 "TaskStatus.resolved" repr。"""
    if isinstance(status, TaskStatus):
        return status.value
    return str(status)


def _publish_result_ready(*, case_id: str, complaint_id: str, session_id: str) -> None:
    key = case_id or complaint_id
    if not key or key in _result_ready_published:
        return
    _result_ready_published.add(key)
    payload = {
        "case_id": case_id or complaint_id,
        "complaint_no": complaint_id or case_id,
        "trace_id": f"result-ready-{key}",
    }
    publisher.publish("complaint.result.ready", payload)
    logger.info(
        "complaint_result_ready_published",
        case_id=payload["case_id"],
        complaint_no=payload["complaint_no"],
        session_id=session_id,
    )


async def sample_backflow_node(state: ComplaintSessionState) -> dict:
    """样本回流：回写会话与人工复核真值，桥失败入队、节点不阻塞图。"""
    mcp = get_mcp_client()
    task_status = _task_status_value(state.task_status)
    payload = {
        "session_id": state.session_id,
        "task_status": task_status,
        "verdict": state.verdict_draft.verdict.value if state.verdict_draft else None,
    }
    logger.info(
        "sample_backflow_start",
        session_id=state.session_id,
        task_status=task_status,
        verdict=payload["verdict"],
    )

    for tool in ("write_session", "write_human_review_truth"):
        # 单层重试：桥内 3 次 HTTP + 用尽入队（A5013），节点每 tool 只调一次。
        # 桥正常路径永不抛异常；此处仅兜底桥外传输/分发故障，保证图不中断。
        try:
            result = await mcp.call(_SERVER, tool, payload)
            logger.info("sample_backflow_ok", session_id=state.session_id, tool=tool)
            if tool == "write_session" and isinstance(result, dict) and result.get(
                "persisted"
            ):
                case_id = str(
                    result.get("case_id")
                    or (state.case_ids[0] if state.case_ids else "")
                    or state.complaint_id
                    or ""
                )
                complaint_id = str(
                    result.get("complaint_id") or state.complaint_id or case_id
                )
                _publish_result_ready(
                    case_id=case_id,
                    complaint_id=complaint_id,
                    session_id=state.session_id,
                )
        except (MCPTimeoutError, MCPError) as e:
            logger.error(
                "sample_backflow_failed",
                session_id=state.session_id,
                tool=tool,
                error=str(e),
            )
    return {}

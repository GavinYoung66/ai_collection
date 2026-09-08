"""judgment_fanout 研判扇出节点。

FATAL-2：作为 reflection 之后的并行分发入口，仅设置 task_status=judging，
不做业务推理。三条出边（→fact / →compliance / →user_demand）由 4.12 builder 配置。
"""
from __future__ import annotations

from app.models.state import ComplaintSessionState, TaskStatus


async def judgment_fanout_node(state: ComplaintSessionState) -> dict:
    """研判扇出入口：仅设置 judging 状态。"""
    return {"task_status": TaskStatus.judging.value}

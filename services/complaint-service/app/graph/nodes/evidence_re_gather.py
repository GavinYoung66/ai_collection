"""evidence_re_gather 重新取证扇出节点。

作为证据重采流程的并行触发入口：
- 设置 task_status=evidence_gathering
- 清除上一轮 exception_info 标记
本批仅实现节点函数；两条出边（→evidence_case、→evidence_call_qc）由批次6 builder 配置。
"""
from __future__ import annotations

from app.models.state import ComplaintSessionState, TaskStatus


async def evidence_re_gather_node(state: ComplaintSessionState) -> dict:
    """重新取证扇出入口。"""
    return {
        "task_status": TaskStatus.evidence_gathering.value,
        "exception_info": None,
    }

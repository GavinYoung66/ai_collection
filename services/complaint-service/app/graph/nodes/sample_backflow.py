"""sample_backflow 样本回流节点。

调用 MCP write_session + write_human_review_truth 回写样本。
失败自动重试 3 次；仍失败告警不阻塞流程。
"""
from __future__ import annotations

import asyncio
import structlog

from app.api.deps import get_mcp_client
from app.mcp.errors import MCPError, MCPTimeoutError
from app.models.state import ComplaintSessionState

logger = structlog.get_logger()

_SERVER = "data_platform"
_MAX_RETRIES = 3


async def sample_backflow_node(state: ComplaintSessionState) -> dict:
    """样本回流：回写会话与人工复核真值，失败重试不阻塞。"""
    mcp = get_mcp_client()
    payload = {
        "session_id": state.session_id,
        "task_status": state.task_status,
        "verdict": state.verdict_draft.verdict.value if state.verdict_draft else None,
    }

    for tool in ("write_session", "write_human_review_truth"):
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await mcp.call(_SERVER, tool, payload)
                break
            except (MCPTimeoutError, MCPError) as e:
                logger.warning(
                    "sample_backflow_retry", tool=tool, attempt=attempt, error=str(e)
                )
                if attempt == _MAX_RETRIES:
                    logger.error("sample_backflow_exhausted", tool=tool)
                else:
                    await asyncio.sleep(0)
    return {}

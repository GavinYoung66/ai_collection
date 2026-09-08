"""落库前脱敏工具（10.2）。

在 complaint_agent_logs / llm_call_logs 落库前调用，确保明文 PII 不入库。
复用 common.security.mask.mask_text。
"""
from __future__ import annotations

from typing import Any

from common.security.mask import mask_text


def mask_before_persist(text: Any) -> str:
    """落库前脱敏：对文本执行 PII 脱敏。

    - str：mask_text 脱敏
    - 其他类型：str() 后脱敏
    - None / 空：原样返回
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)
    if not text:
        return text
    return mask_text(text)


def has_plaintext_pii(text: str) -> bool:
    """检测文本是否含明文 PII（通过 mask 前后是否变化判断）。"""
    if not isinstance(text, str) or not text:
        return False
    return mask_text(text) != text


def prepare_agent_log(
    node_name: str,
    input_summary: str,
    output_summary: str,
    *,
    started_at: Any = None,
    duration_ms: int = 0,
) -> dict[str, Any]:
    """构造节点/Agent 日志条目：input/output 落库前脱敏。

    图节点与 stub service 写入 agent_logs 时应走此入口（10.2 挂钩点）。
    """
    from datetime import datetime, timezone

    return {
        "node_name": node_name,
        "input_summary": mask_before_persist(input_summary),
        "output_summary": mask_before_persist(output_summary),
        "started_at": started_at or datetime.now(timezone.utc),
        "duration_ms": duration_ms,
    }

"""Prompt 版本元数据：组装写入 Langfuse span metadata 的键值。

纯函数 + 只读 loader，不依赖 Langfuse 是否启用，便于在未启用时直接单测
（规格要求：Langfuse 未启用时 MUST 静默跳过且不抛错，metadata 组装仍需可断言）。
"""
from __future__ import annotations

from common.prompt.loader import load_prompt_sections


def prompt_metadata(prompt_id: str, prompt_version: str) -> dict[str, str]:
    """组装 span metadata；键名固定为 prompt_id / prompt_version。"""
    return {"prompt_id": prompt_id, "prompt_version": prompt_version}


def resolve_prompt_metadata(
    prompt_id: str,
    service: str,
    *,
    session_id: str | None = None,
) -> dict[str, str]:
    """按实际加载到的 YAML 解析 prompt_id/version（给定 session_id 时走 A/B）。"""
    spec = load_prompt_sections(prompt_id, service, session_id=session_id)
    return prompt_metadata(
        str(spec.get("prompt_id") or prompt_id),
        str(spec.get("version") or ""),
    )

"""LLM 本地 Prompt 文件包（dialog / fallback 话术）。

qc_review 已迁至 `common/prompt` + `services/qc-service/app/prompt/templates`。
"""
from .local import load_dialog_prompt, load_fallback_scripts, load_yaml

__all__ = ["load_dialog_prompt", "load_fallback_scripts", "load_yaml"]

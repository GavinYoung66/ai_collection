"""Prompt 模板加载器。"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml
import structlog

logger = structlog.get_logger()

_PROMPTS_DIR = Path(__file__).parent


@functools.lru_cache(maxsize=8)
def load_yaml(filename: str) -> dict:
    """加载 prompts 目录下的 YAML 文件，带缓存。"""
    filepath = _PROMPTS_DIR / filename
    if not filepath.exists():
        logger.error("prompt_file_not_found", path=str(filepath))
        return {}
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_dialog_prompt() -> dict:
    """加载对话应答 Prompt 模板。"""
    return load_yaml("dialog.yml")


def load_fallback_scripts() -> list[dict]:
    """加载兜底话术库。"""
    data = load_yaml("fallback_scripts.yml")
    return data.get("rules", [])


def load_qc_review_prompt() -> dict:
    """加载质检语义复核 Prompt（qc_review_v1）。"""
    return load_yaml("qc_review.yml")

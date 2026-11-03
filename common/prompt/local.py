"""本地 Prompt 资源文件加载（dialog / fallback 话术）。

T30 统一 Prompt 加载体系后的唯一本地文件加载入口：
- ``files/dialog.yml``：对话应答 Prompt 模板；
- ``files/fallback_scripts.yml``：兜底话术库（rule_engine 内置规则）。
结构化 Prompt（prompt_id@version + 七段 profile）走 ``common.prompt.loader``。
"""
from __future__ import annotations

import functools
from pathlib import Path

import structlog
import yaml

logger = structlog.get_logger()

_FILES_DIR = Path(__file__).parent / "files"


@functools.lru_cache(maxsize=8)
def load_yaml(filename: str) -> dict:
    """加载 files 目录下的 YAML 文件，带缓存。"""
    filepath = _FILES_DIR / filename
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

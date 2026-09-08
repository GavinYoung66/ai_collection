"""Prompt 加载异常类型。"""
from __future__ import annotations


class PromptNotFoundError(Exception):
    """指定 prompt_id（含版本）未找到。"""

    def __init__(self, prompt_id: str, version: str | None = None):
        self.prompt_id = prompt_id
        self.version = version
        super().__init__(f"Prompt not found: {prompt_id}" + (f"@{version}" if version else ""))


class PromptStructureError(Exception):
    """Prompt 七段结构不齐全。"""

    def __init__(self, prompt_id: str, missing: list[str]):
        self.prompt_id = prompt_id
        self.missing = missing
        super().__init__(f"Prompt '{prompt_id}' missing sections: {missing}")

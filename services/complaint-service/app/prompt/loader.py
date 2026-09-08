"""Prompt 统一加载器。

load_prompt(prompt_id@version, **context_vars) -> str：
- dev：从 app/prompt/templates/ 加载 YAML
- prod：Nacos 加载（本批 stub，接口预留）
- 支持 prompt_id@version 语义化版本引用
- 可选 session_id=：无 @version 时先经 A/B 选版本再加载
- Jinja2 逐段变量替换渲染
- 校验七段结构：role/task/input_constraint/output_schema/prohibitions/fallback/enum_constraints
  以及 few_shot 键必须存在
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import structlog
import yaml
from jinja2 import Template

from app.prompt.errors import PromptNotFoundError, PromptStructureError

logger = structlog.get_logger()

_TEMPLATES_DIR = Path(__file__).parent / "templates"

# 七段结构（对齐 PRD 8.1；权威第七段为 enum_constraints，非 examples）
_REQUIRED_SECTIONS = (
    "role",
    "task",
    "input_constraint",
    "output_schema",
    "prohibitions",
    "fallback",
    "enum_constraints",
)
# 额外必须存在的键
_REQUIRED_EXTRA_KEYS = ("few_shot",)


def load_prompt(prompt_id: str, **context_vars: Any) -> str:
    """加载并渲染 Prompt。

    Args:
        prompt_id: prompt_id 或 prompt_id@version
        session_id: 可选，无 @version 时用于 A/B 选版本（须已 configure_ab；未配置则抛 KeyError）
        **context_vars: Jinja2 模板变量

    Returns:
        渲染后的 prompt 字符串
    """
    pid, version = _parse_prompt_id(prompt_id)
    session_id = context_vars.pop("session_id", None)

    # 无显式版本且提供 session_id 时，经 A/B 选版本；未配置则抛错（不静默回退）
    if version is None and session_id is not None:
        from app.prompt.ab import select_version

        version = select_version(pid, session_id)

    data = _load_yaml(pid, version)
    _validate_sections(pid, data)
    return _render(data, context_vars)


def _parse_prompt_id(prompt_id: str) -> tuple[str, Optional[str]]:
    """解析 prompt_id@version。"""
    if "@" in prompt_id:
        pid, ver = prompt_id.split("@", 1)
        return pid, ver
    return prompt_id, None


def _load_yaml(prompt_id: str, version: Optional[str]) -> dict:
    """加载 YAML。dev 读本地文件；prod Nacos stub。"""
    # TODO(prod): 接入 Nacos；本批走本地文件
    return _load_from_local(prompt_id, version)


def _load_from_local(prompt_id: str, version: Optional[str]) -> dict:
    """从 templates/ 目录加载 YAML。"""
    # 收集所有 prompt_id 匹配的文件
    matched: list[tuple[Path, dict]] = []
    for path in _TEMPLATES_DIR.glob("*.yaml"):
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not isinstance(data, dict):
            continue
        if data.get("prompt_id") == prompt_id:
            matched.append((path, data))

    if not matched:
        raise PromptNotFoundError(prompt_id, version)

    if version is not None:
        for path, data in matched:
            if data.get("version") == version:
                return data
        # 找到 prompt_id 但版本不匹配
        raise PromptNotFoundError(prompt_id, version)

    # 无版本：取第一个（单文件策略，design D6）
    return matched[0][1]


def _validate_sections(prompt_id: str, data: dict) -> None:
    """校验七段 + few_shot 键齐全。"""
    sections = data.get("sections", {})
    if not isinstance(sections, dict):
        raise PromptStructureError(prompt_id, list(_REQUIRED_SECTIONS) + list(_REQUIRED_EXTRA_KEYS))
    missing = [s for s in _REQUIRED_SECTIONS if s not in sections]
    missing += [k for k in _REQUIRED_EXTRA_KEYS if k not in sections]
    if missing:
        raise PromptStructureError(prompt_id, missing)


def _render(data: dict, context_vars: dict) -> str:
    """逐段 Jinja2 渲染后拼接（含 few_shot）。"""
    sections = data["sections"]
    render_order = list(_REQUIRED_SECTIONS) + list(_REQUIRED_EXTRA_KEYS)
    rendered_parts: list[str] = []
    for name in render_order:
        value = sections.get(name, "")
        if isinstance(value, str) and value:
            try:
                rendered = Template(value).render(**context_vars)
            except Exception as e:
                logger.warning("jinja_render_failed", section=name, error=str(e))
                rendered = value
            rendered_parts.append(f"## {name}\n{rendered}")
        elif isinstance(value, list):
            text = "\n".join(str(item) for item in value)
            rendered_parts.append(f"## {name}\n{text}")
        else:
            rendered_parts.append(f"## {name}\n{value}")
    return "\n\n".join(rendered_parts)


def _load_from_nacos(prompt_id: str, version: Optional[str]) -> dict:
    """prod Nacos 加载（本批 stub，未对接真实 Nacos）。"""
    raise NotImplementedError(f"Nacos loading not implemented yet for {prompt_id}")

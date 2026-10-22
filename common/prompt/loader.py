"""Prompt 统一加载器（平台公共入口）。

load_prompt(prompt_id@version, service, **context_vars) -> str：
- dev：按 service 解析到该服务模板目录加载 YAML
- prod：Nacos 加载（本批 stub，接口预留）
- 支持 prompt_id@version 语义化版本引用
- 可选 session_id=：无 @version 时先经 A/B 选版本再加载
- Jinja2 逐段变量替换渲染
- 按 YAML 顶层 profile 校验（缺省 standard）：
  standard 七段 role/task/input_constraint/output_schema/prohibitions/fallback/enum_constraints
  simple 四段 role/task/input_constraint/output_schema
  两种 profile 均要求 few_shot 键存在（可空）
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

import structlog
import yaml
from jinja2 import Template

from common.prompt.errors import PromptNotFoundError, PromptStructureError

logger = structlog.get_logger()

# 仓库根：common/prompt/loader.py -> parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[2]

# 服务名 -> 模板目录（模板本地化，引擎公共化）
_SERVICE_TEMPLATE_DIRS = {
    "complaint-service": _REPO_ROOT / "services/complaint-service/app/prompt/templates",
    "qc-service": _REPO_ROOT / "services/qc-service/app/prompt/templates",
    "rag-service": _REPO_ROOT / "services/rag-service/conf",
}
# 容器内路径差异：设置后直接使用该目录（不区分 service）
_TEMPLATES_DIR_ENV = "PROMPT_TEMPLATES_DIR"

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

# 各 profile 必须包含的 sections 键
_PROFILE_REQUIRED_SECTIONS = {
    "standard": _REQUIRED_SECTIONS,
    "simple": ("role", "task", "input_constraint", "output_schema"),
}


def load_prompt(prompt_id: str, service: str, **context_vars: Any) -> str:
    """加载并渲染 Prompt。

    Args:
        prompt_id: prompt_id 或 prompt_id@version
        service: 调用方服务名，用于解析模板目录
        session_id: 可选，无 @version 时用于 A/B 选版本（须已 configure_ab；未配置则抛 KeyError）
        **context_vars: Jinja2 模板变量

    Returns:
        渲染后的 prompt 字符串
    """
    pid, version = _parse_prompt_id(prompt_id)
    session_id = context_vars.pop("session_id", None)
    templates_dir = _resolve_templates_dir(service)

    # 无显式版本且提供 session_id 时，经 A/B 选版本；未配置则抛错（不静默回退）
    if version is None and session_id is not None:
        from common.prompt.ab import select_version

        version = select_version(pid, session_id)

    data = _load_yaml(pid, version, templates_dir)
    _validate_sections(pid, data)
    return _render(data, context_vars)


def load_prompt_sections(
    prompt_id: str,
    service: str,
    session_id: str | None = None,
) -> dict:
    """加载并校验 Prompt，返回原始元数据与 sections（不做 Jinja 渲染）。

    供需要自行组装系统提示的调用方使用（如 qc 的 build_system_prompt），
    避免通用渲染格式与既有提示词文本产生差异。

    Args:
        prompt_id: prompt_id 或 prompt_id@version
        service: 调用方服务名，用于解析模板目录
        session_id: 可选，无 @version 时用于 A/B 选版本（须已 configure_ab）

    Returns:
        含 prompt_id / version / profile / sections 的字典
    """
    pid, version = _parse_prompt_id(prompt_id)
    templates_dir = _resolve_templates_dir(service)

    if version is None and session_id is not None:
        from common.prompt.ab import select_version

        version = select_version(pid, session_id)

    data = _load_yaml(pid, version, templates_dir)
    _validate_sections(pid, data)
    return data


def _resolve_templates_dir(service: str) -> Path:
    """按 service 解析模板目录；环境变量覆盖用于容器内路径差异。"""
    override = os.environ.get(_TEMPLATES_DIR_ENV)
    if override:
        return Path(override)
    if service not in _SERVICE_TEMPLATE_DIRS:
        raise ValueError(f"Unknown prompt service: {service}")
    return _SERVICE_TEMPLATE_DIRS[service]


def _parse_prompt_id(prompt_id: str) -> tuple[str, Optional[str]]:
    """解析 prompt_id@version。"""
    if "@" in prompt_id:
        pid, ver = prompt_id.split("@", 1)
        return pid, ver
    return prompt_id, None


def _load_yaml(prompt_id: str, version: Optional[str], templates_dir: Path) -> dict:
    """加载 YAML。dev 读本地文件；prod Nacos stub。"""
    # TODO(prod): 接入 Nacos；本批走本地文件
    return _load_from_local(prompt_id, version, templates_dir)


def _load_from_local(prompt_id: str, version: Optional[str], templates_dir: Path) -> dict:
    """从服务模板目录加载 YAML。"""
    # 收集所有 prompt_id 匹配的文件
    matched: list[tuple[Path, dict]] = []
    for path in templates_dir.glob("*.yaml"):
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
    """按 profile 校验 sections 与 few_shot 键齐全。"""
    profile = data.get("profile") or "standard"
    required = _PROFILE_REQUIRED_SECTIONS.get(profile)
    if required is None:
        raise PromptStructureError(prompt_id, [f"unknown profile: {profile}"])

    sections = data.get("sections", {})
    if not isinstance(sections, dict):
        raise PromptStructureError(prompt_id, list(required) + list(_REQUIRED_EXTRA_KEYS))
    missing = [s for s in required if s not in sections]
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
        elif isinstance(value, dict):
            # 结构化 section（如 qc 的 output_schema/enum_constraints）按 JSON 渲染，
            # 避免 Python repr 的单引号导致下游无法按 JSON 解析。
            rendered_parts.append(
                f"## {name}\n{json.dumps(value, ensure_ascii=False, indent=2)}"
            )
        else:
            rendered_parts.append(f"## {name}\n{value}")
    return "\n\n".join(rendered_parts)


def _load_from_nacos(prompt_id: str, version: Optional[str]) -> dict:
    """prod Nacos 加载（本批 stub，未对接真实 Nacos）。"""
    raise NotImplementedError(f"Nacos loading not implemented yet for {prompt_id}")

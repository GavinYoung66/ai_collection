"""Prompt 加载器单元测试。"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.prompt import loader
from app.prompt.errors import PromptNotFoundError, PromptStructureError


def _write_template(tmp: Path, name: str, data: dict) -> Path:
    p = tmp / name
    with open(p, "w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)
    return p


def _base_sections() -> dict:
    return {
        "role": "你是助手，处理 {{ case_id }}",
        "task": "完成任务，用户说 {{ user_text }}",
        "input_constraint": "输入约束",
        "output_schema": "SchemaX",
        "prohibitions": ["禁止项1"],
        "fallback": "兜底规则",
        "enum_constraints": ["枚举约束1"],
        "few_shot": "示例 {{ case_id }}",
    }


def test_jinja_variables_rendered(tmp_path, monkeypatch):
    """声明的 Jinja 变量被替换，不含未渲染 {{ }}。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    out = loader.load_prompt("test_pid", case_id="C123", user_text="hello")
    assert "C123" in out
    assert "hello" in out
    assert "{{" not in out
    assert "}}" not in out


def test_version_match_success(tmp_path, monkeypatch):
    """@version 与文件内 version 一致可加载。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "2.1.0", "sections": _base_sections(),
    })
    out = loader.load_prompt("test_pid@2.1.0")
    assert isinstance(out, str) and out.strip()


def test_version_mismatch_raises(tmp_path, monkeypatch):
    """错误版本抛 PromptNotFoundError。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "2.1.0", "sections": _base_sections(),
    })
    with pytest.raises(PromptNotFoundError):
        loader.load_prompt("test_pid@9.9.9")


def test_missing_enum_constraints_raises(tmp_path, monkeypatch):
    """缺 enum_constraints 抛 PromptStructureError。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    secs = _base_sections()
    del secs["enum_constraints"]
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": secs,
    })
    with pytest.raises(PromptStructureError):
        loader.load_prompt("test_pid")


def test_missing_few_shot_raises(tmp_path, monkeypatch):
    """缺 few_shot 抛 PromptStructureError。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    secs = _base_sections()
    del secs["few_shot"]
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": secs,
    })
    with pytest.raises(PromptStructureError):
        loader.load_prompt("test_pid")


def test_session_id_triggers_ab(tmp_path, monkeypatch):
    """提供 session_id 时经 A/B 选版本（单文件策略下仍可加载）。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    from app.prompt import ab
    ab._ab_config.clear()
    ab.configure_ab("test_pid", {"1.0.0": 100})
    out = loader.load_prompt("test_pid", session_id="s1")
    assert isinstance(out, str) and out.strip()


def test_session_id_without_ab_config_raises(tmp_path, monkeypatch):
    """提供 session_id 但未配置 A/B 时抛 KeyError，不静默回退。"""
    monkeypatch.setattr(loader, "_TEMPLATES_DIR", tmp_path)
    _write_template(tmp_path, "test_pid.yaml", {
        "prompt_id": "test_pid", "version": "1.0.0", "sections": _base_sections(),
    })
    from app.prompt import ab
    ab._ab_config.clear()
    with pytest.raises(KeyError):
        loader.load_prompt("test_pid", session_id="s1")

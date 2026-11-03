"""批次 6 架构收敛的不变量测试（T24/T26/T27）。

这些任务属于部署编排/文档/死代码清理，不对运行时契约产生行为变化，
因此用源码与编排文件的结构断言钉住整改结果。
"""
from __future__ import annotations

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SHELL_SERVICES = ("call-service", "case-service", "review-service")
_PROFILE = "shell-services"


def _load_compose(name: str) -> dict:
    with open(_ROOT / name, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def test_t24_shell_services_are_non_default_profiles() -> None:
    """call/case/review 仅在显式 profile/服务名时才启动。"""
    for compose_file in ("docker-compose.dev.yml", "docker-compose.smoke-services.yml"):
        services = _load_compose(compose_file)["services"]
        for name in _SHELL_SERVICES:
            assert services[name]["profiles"] == [_PROFILE], (compose_file, name)

        # 核心服务必须保持默认启动（不得被误加 profile）
        for name in ("complaint-service", "qc-service", "data-platform"):
            assert "profiles" not in services[name], (compose_file, name)

        # 默认启动的服务不得依赖按需启动的空壳服务，否则 profile 无法真正隔离
        for service_name, spec in services.items():
            depends_on = spec.get("depends_on") or {}
            if isinstance(depends_on, dict):
                assert not set(depends_on) & set(_SHELL_SERVICES), service_name


def test_t24_root_compose_does_not_ship_shell_services() -> None:
    """根编排只含基础设施，默认 `docker compose up` 不会拉起空壳服务。"""
    root = _load_compose("docker-compose.yml")
    assert set(root.get("services") or {}) & set(_SHELL_SERVICES) == set()


def test_t26_readme_marks_llm_orchestrator_as_in_process() -> None:
    """架构图必须把 LLM 编排器标为进程内，且不得再写成独立网关/独立进程。"""
    for name in ("README.md", "README_EN.md"):
        text = (_ROOT / name).read_text(encoding="utf-8")
        assert "LLM 编排器（进程内）" in text or "LLM orchestrator (in-process)" in text
        assert "大模型网关" not in text
        assert "LLM gateway" not in text
        assert "非独立进程" in text or "not a separate process" in text


def test_t26_readme_has_no_standalone_llm_gateway_claim() -> None:
    """边界：注释不得与图例自相矛盾（出现“独立网关/独立服务”即失败）。"""
    text = (_ROOT / "README.md").read_text(encoding="utf-8")
    section = text.split("## 整体架构", 1)[1].split("## ", 1)[0]
    assert "独立网关" not in section.replace("不是独立服务进程或独立网关", "")
    assert "独立进程" not in section.replace("非独立进程", "")


def test_t27_dead_llm_breaker_module_is_removed() -> None:
    """@llm_breaker 全局单实例已由 per-key breaker 取代，模块与导出必须删除。"""
    import common.llm as llm_pkg

    assert not (_ROOT / "common" / "llm" / "circuit_breaker.py").exists()
    assert "llm_breaker" not in getattr(llm_pkg, "__all__", [])
    assert not hasattr(llm_pkg, "llm_breaker")


def test_t27_no_code_references_removed_module() -> None:
    """异常/回归：代码中不得再 import 已删除模块。"""
    offenders = []
    for path in _ROOT.rglob("*.py"):
        if path == Path(__file__).resolve():
            continue
        if any(part in {".git", ".venv", "node_modules"} for part in path.parts):
            continue
        if "llm.circuit_breaker" in path.read_text(encoding="utf-8", errors="ignore"):
            offenders.append(str(path.relative_to(_ROOT)))
    assert offenders == []

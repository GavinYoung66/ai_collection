"""LLM 模型路由策略。

按 Agent 角色选型模型，支持四级熔断降级链。
模型名走 model_router.yaml 配置，不硬编码在业务代码。

四级降级链：primary -> fallback_1 -> fallback_2 -> rule_based -> circuit_break
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog
import yaml

logger = structlog.get_logger()

_ROUTER_YAML = Path(__file__).parent / "model_router.yaml"


class ModelRouter:
    """模型路由器。"""

    def __init__(self, config_path: Optional[Path] = None):
        path = config_path or _ROUTER_YAML
        with open(path, "r", encoding="utf-8") as f:
            self._config = yaml.safe_load(f)
        self._routes = self._config.get("routes", {})
        self._breaker_cfg = self._config.get("breaker", {})
        self._degradation = self._config.get("degradation", {})

    def route(self, agent_role: str) -> str:
        """返回 agent_role 对应的 primary 模型名。

        orchestrator 返回空串（不调 LLM）。
        """
        route = self._routes.get(agent_role)
        if route is None:
            raise ValueError(f"unknown agent_role: {agent_role}")
        if not route.get("use_llm", True):
            return ""
        return route["primary"]

    def fallback_chain(self, agent_role: str) -> list[str]:
        """返回 agent_role 的降级链（不含 primary）。"""
        route = self._routes.get(agent_role, {})
        return list(route.get("fallback_chain", []))

    def degradation_flags(self, agent_role: str) -> dict:
        """返回降级标记（合规/判责在降级时需标记）。"""
        return dict(self._degradation.get(agent_role, {}))

    def breaker_config(self, model: str) -> dict:
        """返回指定模型的熔断阈值配置。"""
        key = model.replace("-", "_")
        return dict(self._breaker_cfg.get(key, {}))

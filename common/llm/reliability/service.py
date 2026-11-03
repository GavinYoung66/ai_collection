"""宿主服务名解析（单一职责：per-key 前缀的单一事实源）。

per-key 熔断/限流/降级 key 派生依赖本模块；禁止无前缀全局 key——
那会复现"一个 key 干掉全平台"。
"""
from __future__ import annotations

from common.config.settings import settings

_default_service: str | None = None


def set_default_service(name: str | None) -> None:
    """设置宿主服务名（csmp/rag/qc），服务启动时调用一次。"""
    global _default_service
    _default_service = (name or "").strip() or None


def resolve_service_name() -> str:
    """解析宿主服务名：显式设置优先，其次 settings.SERVICE_NAME；均无则 fail-fast。

    绝不静默回退到无前缀 key——那会复现"一个 key 干掉全平台"。
    """
    if _default_service:
        return _default_service
    name = (getattr(settings, "SERVICE_NAME", "") or "").strip()
    if not name:
        raise RuntimeError(
            "服务名未配置：请先 set_default_service('csmp'|'rag'|'qc') "
            "或设置 settings.SERVICE_NAME"
        )
    return name

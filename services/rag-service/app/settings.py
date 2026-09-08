"""rag-service 运行时配置入口。

推荐读法（择一，按字段归属）：
- 产品开关 / `KB_*`：`from app.rag_settings import rag_settings`
- 平台共享（PG / Redis / 跨服务 LLM Key 等）：
  `from common.config.settings import settings as platform_settings`
- 兼容统一读（旧代码 / 测试 monkeypatch）：`from app.settings import settings`

本模块同时再导出 `rag_settings` 与 `platform_settings`，避免「不知道去哪 import」。
"""
from __future__ import annotations

from common.config.settings import settings as platform_settings
from app.rag_settings import RagSettings, rag_settings

_RAG_FIELDS = frozenset(RagSettings.model_fields.keys())


class _SettingsProxy:
    """统一 `settings.X` 读法；测试可 setattr 覆盖（写入 `_overrides`）。

    Deprecated for new code: prefer explicit `rag_settings` / `platform_settings`
    imports (ARCH-05). This proxy remains for backward-compatible monkeypatch.
    """

    def __init__(self) -> None:
        object.__setattr__(self, "_overrides", {})

    def __getattr__(self, item: str):
        overrides = object.__getattribute__(self, "_overrides")
        if item in overrides:
            return overrides[item]
        if item in _RAG_FIELDS:
            return getattr(rag_settings, item)
        return getattr(platform_settings, item)

    def __setattr__(self, key: str, value) -> None:
        if key.startswith("_"):
            object.__setattr__(self, key, value)
            return
        object.__getattribute__(self, "_overrides")[key] = value

    def __dir__(self) -> list[str]:
        names = set(dir(platform_settings)) | set(_RAG_FIELDS) | set(
            object.__getattribute__(self, "_overrides")
        )
        return sorted(names)


settings = _SettingsProxy()

__all__ = [
    "settings",
    "rag_settings",
    "platform_settings",
    "RagSettings",
]

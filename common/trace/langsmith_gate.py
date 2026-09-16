"""LangSmith 链路追踪统一闸门（跨服务唯一开关）。

约定三条：

1. **唯一开关**：`LANGSMITH_ENABLED`（common Settings，来自 .env + docker-compose）。
   为 false 时全线不建 run、不写 SDK 环境变量、不外链，并主动清除既有 SDK 环境变量。
2. **唯一入口**：各服务不再自行判断，一律调本模块，并传入本服务的 settings 实例
   （complaint-service 等使用专有 Settings 子类；不传则用平台 common Settings）。
3. **降级安全**：任何 LangSmith 异常都不得影响本地链路（本地事件 / 日志为 P0，
   LangSmith 仅为 P1 外链展示）。
"""
from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()

# 关闭时须主动清除的进程环境变量，避免 LangChain 等 SDK 级旁路自行上报
_TRACING_ENV_KEYS = (
    "LANGSMITH_TRACING",
    "LANGCHAIN_TRACING",
    "LANGCHAIN_TRACING_V2",
    "LANGSMITH_API_KEY",
    "LANGSMITH_PROJECT",
    "LANGSMITH_ENDPOINT",
)

_DEFAULT_PROJECT = "ai_collection"


def _default_settings() -> Any:
    from common.config.settings import settings

    return settings


def _field(settings: Any, name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _api_key(settings: Any) -> str:
    return str(_field(settings, "LANGSMITH_API_KEY") or "").strip()


def langsmith_enabled(settings: Any = None) -> bool:
    """统一开关判定：开关为 true 且 Key 非空，才视为启用。"""
    s = settings or _default_settings()
    return bool(_field(s, "LANGSMITH_ENABLED", False)) and bool(_api_key(s))


def langsmith_project(settings: Any = None, fallback: str = _DEFAULT_PROJECT) -> str:
    """项目名：配置为空时回落调用方默认值。"""
    s = settings or _default_settings()
    return str(_field(s, "LANGSMITH_PROJECT") or "").strip() or fallback


def configure_langsmith_env(settings: Any = None) -> None:
    """按统一开关写入 / 清除 LangSmith SDK 环境变量（进程启动或建 run 前调用）。"""
    s = settings or _default_settings()
    if not langsmith_enabled(s):
        for key in _TRACING_ENV_KEYS:
            os.environ.pop(key, None)
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGCHAIN_TRACING_V2"] = "true"
    os.environ["LANGSMITH_API_KEY"] = _api_key(s)
    os.environ["LANGSMITH_PROJECT"] = langsmith_project(s)
    endpoint = str(_field(s, "LANGSMITH_ENDPOINT") or "").strip()
    if endpoint:
        os.environ["LANGSMITH_ENDPOINT"] = endpoint


def langsmith_url(
    run_id: str | None,
    settings: Any = None,
    fallback_project: str = _DEFAULT_PROJECT,
) -> str | None:
    """外链构造：开关关 / 无 run id / 无模板 / 模板缺占位符 均返回 None，避免 404 死链。"""
    if not run_id:
        return None
    s = settings or _default_settings()
    if not langsmith_enabled(s):
        return None
    tmpl = str(_field(s, "LANGSMITH_TRACE_URL_TEMPLATE") or "").strip()
    if not tmpl:
        return None
    if "{run_id}" not in tmpl and "{project}" not in tmpl:
        return None
    try:
        return tmpl.format(run_id=run_id, project=langsmith_project(s, fallback_project))
    except Exception:  # noqa: BLE001 — 模板非法一律隐藏外链
        return None


class LangSmithRun:
    """可选包裹一次链路执行；关闭或异常时退化为空操作，不影响本地链路。"""

    def __init__(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        settings: Any = None,
        fallback_project: str = _DEFAULT_PROJECT,
    ):
        self._root = None
        self.run_id: str | None = None
        s = settings or _default_settings()
        if not langsmith_enabled(s):
            return
        try:
            configure_langsmith_env(s)
            from langsmith.run_trees import RunTree

            self._root = RunTree(
                name=name,
                run_type="chain",
                inputs=inputs,
                project_name=langsmith_project(s, fallback_project),
            )
            self._root.post()
            self.run_id = str(getattr(self._root, "id", "") or "") or None
        except Exception as e:  # noqa: BLE001 — P1 能力失败不影响 P0 链路
            logger.warning("langsmith_run_init_failed", run=name, error=str(e))
            self._root = None
            self.run_id = None

    def end(
        self,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self._root is None:
            return
        try:
            if error:
                self._root.end(error=error)
            else:
                self._root.end(outputs=outputs or {})
            self._root.patch()
        except Exception as e:  # noqa: BLE001
            logger.warning("langsmith_run_end_failed", error=str(e))

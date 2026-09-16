"""Langfuse 链路追踪统一闸门（跨服务唯一开关）。

与 `langsmith_gate` 同构，约定三条：

1. **唯一开关**：`LANGFUSE_ENABLED`（common Settings，来自 .env + docker-compose）。
   为 false 时全线不建 span、不写 SDK 环境变量，并主动清除既有 SDK 环境变量。
2. **唯一入口**：各服务不再自行判断，一律调本模块，并传入本服务的 settings 实例。
3. **降级安全**：任何 Langfuse 异常都不得影响本地链路（本地事件 / 日志为 P0，
   Langfuse 仅为 P1 外链展示）。
"""
from __future__ import annotations

import os
from typing import Any

import structlog

logger = structlog.get_logger()

# 关闭时须主动清除的进程环境变量，避免 SDK 级旁路自行上报：
# - LANGFUSE_TRACING_ENABLED：SDK 自带开关（默认 "true"）
# - LANGFUSE_HOST：LANGFUSE_BASE_URL 的旧名，SDK 仍会读取
# - LANGFUSE_BASE_URL：不清除则 SDK 回落到默认的 https://cloud.langfuse.com（静默打到云）
_TRACING_ENV_KEYS = (
    "LANGFUSE_TRACING_ENABLED",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "LANGFUSE_BASE_URL",
    "LANGFUSE_HOST",
)

_DEFAULT_BASE_URL = "http://localhost:3000"

_client: Any = None
_client_key: tuple[str, str, str] | None = None


def _default_settings() -> Any:
    from common.config.settings import settings

    return settings


def _field(settings: Any, name: str, default: Any = "") -> Any:
    return getattr(settings, name, default)


def _text(settings: Any, name: str) -> str:
    return str(_field(settings, name) or "").strip()


def langfuse_enabled(settings: Any = None) -> bool:
    """统一开关判定：开关为 true 且 public / secret key 均非空，才视为启用。"""
    s = settings or _default_settings()
    return (
        bool(_field(s, "LANGFUSE_ENABLED", False))
        and bool(_text(s, "LANGFUSE_PUBLIC_KEY"))
        and bool(_text(s, "LANGFUSE_SECRET_KEY"))
    )


def langfuse_base_url(settings: Any = None) -> str:
    """实例地址：配置为空时回落本地自托管默认值，绝不留空让 SDK 打到云。"""
    s = settings or _default_settings()
    return _text(s, "LANGFUSE_BASE_URL") or _DEFAULT_BASE_URL


def configure_langfuse_env(settings: Any = None) -> None:
    """按统一开关写入 / 清除 Langfuse SDK 环境变量（进程启动或建 span 前调用）。"""
    s = settings or _default_settings()
    if not langfuse_enabled(s):
        for key in _TRACING_ENV_KEYS:
            os.environ.pop(key, None)
        return
    base_url = langfuse_base_url(s)
    os.environ["LANGFUSE_TRACING_ENABLED"] = "true"
    os.environ["LANGFUSE_PUBLIC_KEY"] = _text(s, "LANGFUSE_PUBLIC_KEY")
    os.environ["LANGFUSE_SECRET_KEY"] = _text(s, "LANGFUSE_SECRET_KEY")
    os.environ["LANGFUSE_BASE_URL"] = base_url
    # 旧名同步，避免历史残留指向别的实例
    os.environ["LANGFUSE_HOST"] = base_url


def get_langfuse_client(settings: Any = None) -> Any:
    """惰性单例客户端；关闭 / 未装 SDK / 初始化失败一律返回 None（P1 降级）。"""
    global _client, _client_key
    s = settings or _default_settings()
    if not langfuse_enabled(s):
        return None
    key = (
        _text(s, "LANGFUSE_PUBLIC_KEY"),
        _text(s, "LANGFUSE_SECRET_KEY"),
        langfuse_base_url(s),
    )
    if _client is not None and _client_key == key:
        return _client
    try:
        configure_langfuse_env(s)
        from langfuse import Langfuse

        _client = Langfuse(
            public_key=key[0],
            secret_key=key[1],
            base_url=key[2],
            tracing_enabled=True,
        )
        _client_key = key
        logger.info("langfuse_client_ready", base_url=key[2])
        return _client
    except Exception as e:  # noqa: BLE001 — P1 能力失败不影响 P0 链路
        logger.warning("langfuse_client_init_failed", error=str(e))
        _client = None
        _client_key = None
        return None


class LangfuseSpanHandle:
    """一段 span 的薄包装：统一 end(outputs/error) 语义，异常静默降级。"""

    def __init__(self, span: Any = None):
        self._span = span

    def end(
        self,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        if self._span is None:
            return
        try:
            if error:
                self._span.update(level="ERROR", status_message=error)
            else:
                self._span.update(output=outputs or {})
            self._span.end()
        except Exception as e:  # noqa: BLE001
            logger.warning("langfuse_span_end_failed", error=str(e))


class LangfuseRun(LangfuseSpanHandle):
    """可选包裹一次链路执行；关闭或异常时退化为空操作，不影响本地链路。"""

    def __init__(
        self,
        name: str,
        inputs: dict[str, Any],
        *,
        settings: Any = None,
        metadata: dict[str, Any] | None = None,
    ):
        super().__init__(None)
        s = settings or _default_settings()
        if not langfuse_enabled(s):
            return
        try:
            client = get_langfuse_client(s)
            if client is None:
                return
            self._span = client.start_observation(
                name=name,
                as_type="chain",
                input=inputs,
                metadata={k: v for k, v in (metadata or {}).items() if v},
            )
        except Exception as e:  # noqa: BLE001 — P1 能力失败不影响 P0 链路
            logger.warning("langfuse_run_init_failed", run=name, error=str(e))
            self._span = None

    def create_child(
        self,
        name: str,
        inputs: dict[str, Any] | None = None,
    ) -> LangfuseSpanHandle | None:
        """建子 span；本 run 未启用（或建失败）时返回 None，调用方跳过即可。"""
        if self._span is None:
            return None
        try:
            span = self._span.start_observation(
                name=name,
                as_type="tool",
                input=inputs or {},
            )
            return LangfuseSpanHandle(span)
        except Exception as e:  # noqa: BLE001
            logger.warning("langfuse_child_init_failed", step=name, error=str(e))
            return None

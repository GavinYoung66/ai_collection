"""LLM 模型维限流阈值（单一职责：环境变量可配置的单一事实源）。

invoke_llm 与各服务自有 transport 共享本模块读取同一来源
（``get_llm_rate_limit()`` 是唯一读取入口），禁止各入口再本地硬编码阈值。
"""
from __future__ import annotations

import os

import structlog

logger = structlog.get_logger()

# 默认值沿用改造前硬编码：未配置/非法配置时行为与改造前完全一致（60 次 / 60 秒 / 模型）。
LLM_RATE_LIMIT_DEFAULT = 60  # 每窗口每模型请求数（默认）
LLM_RATE_WINDOW_DEFAULT = 60  # 限流窗口（秒，默认）

# 模块级当前阈值：import 时由环境变量 LLM_RATE_LIMIT / LLM_RATE_WINDOW 解析一次。
_LLM_RATE_LIMIT: int = LLM_RATE_LIMIT_DEFAULT
_LLM_RATE_WINDOW: int = LLM_RATE_WINDOW_DEFAULT


def _parse_positive_int_env(name: str, default: int) -> int:
    """从环境变量解析正整数。

    - 未设置或空串：返回默认值；
    - 0、负数、非数字：保守回退默认值并打 warn，绝不让服务因配置非法而启动失败；
    - 仅两端空白的值（如 ``"60 "``）允许 strip 后解析。
    """
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning(
            "llm_rate_limit_env_invalid_fallback_default",
            env_name=name,
            raw_value=raw,
            fallback=default,
        )
        return default
    if value <= 0:
        logger.warning(
            "llm_rate_limit_env_non_positive_fallback_default",
            env_name=name,
            raw_value=raw,
            fallback=default,
        )
        return default
    return value


def load_llm_rate_limit() -> tuple[int, int]:
    """（重新）从环境变量加载限流阈值到模块级单一事实源，返回 ``(limit, window)``。"""
    global _LLM_RATE_LIMIT, _LLM_RATE_WINDOW
    _LLM_RATE_LIMIT = _parse_positive_int_env(
        "LLM_RATE_LIMIT", LLM_RATE_LIMIT_DEFAULT
    )
    _LLM_RATE_WINDOW = _parse_positive_int_env(
        "LLM_RATE_WINDOW", LLM_RATE_WINDOW_DEFAULT
    )
    return _LLM_RATE_LIMIT, _LLM_RATE_WINDOW


def get_llm_rate_limit() -> tuple[int, int]:
    """返回当前 ``(每窗口请求数, 窗口秒数)``——LLM 限流阈值的唯一读取入口。"""
    return _LLM_RATE_LIMIT, _LLM_RATE_WINDOW


# 进程启动即解析：环境变量在进程启动前注入；未设置时保持默认 60/60。
_LLM_RATE_LIMIT, _LLM_RATE_WINDOW = load_llm_rate_limit()

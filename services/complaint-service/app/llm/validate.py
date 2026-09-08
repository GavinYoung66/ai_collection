"""真路径 LLM 配置校验（DEBT-04 / design D6，与 MCP validate 对称）。"""
from __future__ import annotations

from app.config import settings
from common.llm.reliability import is_placeholder_secret


class LLMConfigError(RuntimeError):
    """USE_MOCK_LLM=false 时缺少可用 API Key。"""


def validate_real_llm_config(*, use_mock: bool | None = None) -> None:
    """Mock 关闭时要求至少一路非占位 Key（DeepSeek 或 DashScope/LLM_API_KEY）。"""
    mock = settings.USE_MOCK_LLM if use_mock is None else use_mock
    if mock:
        return

    deepseek = (
        getattr(settings, "LLM_DEEPSEEK_API_KEY", "")
        or getattr(settings, "DEEPSEEK_API_KEY", "")
        or ""
    ).strip()
    dash = (
        getattr(settings, "LLM_DASHSCOPE_API_KEY", "")
        or getattr(settings, "LLM_API_KEY", "")
        or getattr(settings, "DASHSCOPE_API_KEY", "")
        or ""
    ).strip()
    if is_placeholder_secret(deepseek) and is_placeholder_secret(dash):
        raise LLMConfigError(
            "USE_MOCK_LLM=false requires a real LLM_DEEPSEEK_API_KEY or "
            "LLM_DASHSCOPE_API_KEY / LLM_API_KEY; keep USE_MOCK_LLM=true for Demo"
        )

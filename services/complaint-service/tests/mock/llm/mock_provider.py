"""Mock LLM Provider（T121 薄转发层）。

模块本体已迁入 app/mock/llm/mock_provider.py；旧导入路径
``from tests.mock.llm.mock_provider import ...`` 保持零改动兼容。
"""
from app.mock.llm.mock_provider import (  # noqa: F401
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
    MockLLMProvider,
)

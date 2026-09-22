"""LLM Provider 工厂：按 LLM_PROVIDER 产出 (model, fallback_chain, transport)。"""
from collections.abc import Awaitable, Callable

from app.services.llm_providers.base import LLMAnswer, LLMProvider, build_answer_prompt
from app.services.llm_providers.dashscope_provider import DashScopeProvider
from app.services.llm_providers.deepseek_provider import DeepSeekProvider
from app.services.llm_providers.local_openai_provider import LocalOpenAIProvider
from app.settings import settings

LLMTransport = Callable[[str, list[dict], float], Awaitable[str]]


class LLMFactory:
    """按 `settings.LLM_PROVIDER` 返回主模型、备用链与现网 transport。"""

    @staticmethod
    def create(
        name: str | None = None,
    ) -> tuple[str, list[str], LLMTransport]:
        provider_name = (name or settings.LLM_PROVIDER).lower()
        if provider_name == "dashscope":
            model = (settings.LLM_MODEL or "qwen-plus").strip() or "qwen-plus"
            fallback = [] if model == "qwen-turbo" else ["qwen-turbo"]
            return model, fallback, DashScopeProvider()
        if provider_name == "deepseek":
            return "deepseek-chat", [], DeepSeekProvider()
        if provider_name == "local":
            model = (settings.LLM_MODEL or "").strip() or settings.LLM_MODEL
            return model, [], LocalOpenAIProvider()
        raise ValueError(f"Unsupported LLM provider: {provider_name}")


__all__ = [
    "LLMFactory",
    "LLMProvider",
    "LLMAnswer",
    "DashScopeProvider",
    "DeepSeekProvider",
    "LocalOpenAIProvider",
    "build_answer_prompt",
]

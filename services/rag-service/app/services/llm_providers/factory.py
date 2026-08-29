"""LLM Provider 工厂，按名称创建具体实现（V5，design D8）。"""
from redis.asyncio import Redis

from app.services.llm_providers.base import LLMAnswer, LLMProvider, build_answer_prompt
from app.services.llm_providers.dashscope_provider import DashScopeProvider
from app.services.llm_providers.deepseek_provider import DeepSeekProvider
from app.services.llm_providers.local_openai_provider import LocalOpenAIProvider
from common.config.settings import settings


class LLMFactory:
    """LLM Provider 工厂类。

    根据名称或全局配置 `settings.LLM_PROVIDER` 创建对应实现，
    支持 dashscope（在线，默认）与 local（本地 OpenAI 兼容）。
    """

    @staticmethod
    def create(name: str | None = None, redis: Redis | None = None) -> LLMProvider:
        provider_name = (name or settings.LLM_PROVIDER).lower()
        if provider_name == "dashscope":
            return DashScopeProvider(redis=redis)
        if provider_name == "deepseek":
            return DeepSeekProvider(redis=redis)
        if provider_name == "local":
            return LocalOpenAIProvider(redis=redis)
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

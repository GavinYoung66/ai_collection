"""ASR Provider 工厂，按名称创建具体实现。"""
from app.services.asr_providers.base import ASRProvider
from app.services.asr_providers.dashscope_provider import DashScopeASRProvider
from app.services.asr_providers.whisper_provider import WhisperASRProvider
from app.settings import settings


class ASRFactory:
    """ASR Provider 工厂类。

    根据传入的名称或全局配置 `settings.ASR_PROVIDER` 创建对应的
    ASR 实现实例。支持 `dashscope` 与 `whisper` 两种实现。
    """

    @staticmethod
    def create(name: str | None = None) -> ASRProvider:
        """创建 ASR Provider 实例。

        Args:
            name: Provider 名称，取值 {dashscope, whisper}。
                为 None 时使用 `settings.ASR_PROVIDER`。

        Returns:
            对应的 ASRProvider 实例。

        Raises:
            ValueError: 名称不在支持范围内。
        """
        provider_name = (name or settings.ASR_PROVIDER).lower()
        if provider_name == "dashscope":
            return DashScopeASRProvider()
        if provider_name == "whisper":
            return WhisperASRProvider()
        raise ValueError(f"Unsupported ASR provider: {provider_name}")

"""Whisper ASR Provider 骨架，V2.x 阶段实现。"""
from app.services.asr_providers.base import ASRProvider


class WhisperASRProvider(ASRProvider):
    """OpenAI Whisper ASR 实现骨架。

    预留接口，具体实现计划在 V2.x 版本完成。
    """

    async def transcribe(self, audio_bytes: bytes, fmt: str) -> str:
        """转写音频为文本。

        Raises:
            NotImplementedError: 该 Provider 计划在 V2.x 版本实现。
        """
        raise NotImplementedError("Whisper provider scheduled for V2.x")

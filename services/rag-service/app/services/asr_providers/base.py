"""ASR Provider 抽象基类，定义语音转文本的统一接口。"""
from abc import ABC, abstractmethod


class ASRProvider(ABC):
    """语音识别（ASR）提供方抽象基类。

    所有具体实现（如 DashScope Paraformer、OpenAI Whisper）需实现
    `transcribe` 方法，将音频字节流转换为文本。
    """

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes, fmt: str) -> str:
        """将音频字节流转写为文本。

        Args:
            audio_bytes: 音频文件二进制内容。
            fmt: 音频格式，取值范围 {mp3, wav, m4a}。

        Returns:
            识别出的文本字符串。
        """
        ...
    async def aclose(self) -> None:
        """T60：连接池/资源关闭出口（幂等，默认 no-op）。

        持有 httpx client 的实现（如 DashScope）覆写本方法关闭内部连接池；
        无外部资源的实现保持默认空实现即可。
        """
        return None

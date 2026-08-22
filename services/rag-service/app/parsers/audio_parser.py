"""音频解析器：通过 ASR Provider 转写音频为文字。"""
from pathlib import Path

from app.parsers.base import DocumentParser


class AudioParser(DocumentParser):
    """MP3/WAV/M4A 音频解析，依赖 ASRClient 调用 ASR Provider。"""

    def __init__(self, asr_client=None):
        self._asr_client = asr_client

    async def extract_text(self, file_path: Path) -> str:
        """读取音频 bytes，从后缀取 fmt，调用 ASRClient.transcribe 返回文本。"""
        file_bytes = file_path.read_bytes()
        if self._asr_client is None:
            from app.api.deps import get_asr_client
            self._asr_client = await get_asr_client()
        return await self._asr_client.transcribe(file_bytes, file_path.name)

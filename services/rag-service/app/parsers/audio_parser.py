"""音频解析器：通过 ASR Provider 转写音频为文字（V5 增 AMR/WMA 解码）。"""
from pathlib import Path

from app.parsers.base import DocumentParser
from app.settings import settings


class AudioParser(DocumentParser):
    """MP3/WAV/M4A 音频解析，依赖 ASRClient 调用 ASR Provider；AMR/WMA 先 ffmpeg 解码。"""

    def __init__(self, asr_client=None):
        self._asr_client = asr_client

    async def extract_text(self, file_path: Path) -> str:
        """读取音频 bytes，从后缀取 fmt，调用 ASRClient.transcribe 返回文本。

        - .amr/.wma：`KB_ASR_AMR_WMA_ENABLED=false` → 抛 ValueError（白名单外 A3001，V4 行为）；
          开启 → ffmpeg 解码为 16kHz WAV 字节，再走既有 VAD 静音裁剪 + ≤60s 分片 +
          ASR Provider 转写管线（Provider 契约零改动）；
        - 解码/转写异常 → 捕获为 ValueError 抛出（ingest 转 A3001 或
          `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 降级占位），worker 不卡死。
        """
        ext = file_path.suffix.lower()
        if ext in (".amr", ".wma"):
            if not settings.KB_ASR_AMR_WMA_ENABLED:
                raise ValueError(f"AMR/WMA 解码未开启（KB_ASR_AMR_WMA_ENABLED=false）: {ext}")
            try:
                from app.services.audio_decoder import decode_to_wav

                file_bytes = await decode_to_wav(file_path)
                filename = "decoded.wav"
            except Exception as e:
                raise ValueError(f"AMR/WMA 解码失败: {e}") from e
        else:
            file_bytes = file_path.read_bytes()
            filename = file_path.name

        if self._asr_client is None:
            from app.api.deps import get_asr_client

            self._asr_client = await get_asr_client()
        return await self._asr_client.transcribe(file_bytes, filename)

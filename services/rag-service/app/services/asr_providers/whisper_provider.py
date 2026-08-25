"""Whisper ASR Provider 实现（本地 faster-whisper 模型转写）。"""
import threading

from app.services.asr_providers.base import ASRProvider

# 模块级锁：faster-whisper 模型懒加载的并发保护（并发首次转写只加载一次）
_MODEL_LOCK = threading.Lock()


class WhisperASRProvider(ASRProvider):
    """OpenAI Whisper ASR 实现（本地 faster-whisper，懒加载 tiny 模型）。

    返回与 DashScope Provider 一致的转写文本契约；模型加载/转写异常抛
    RuntimeError，由 asr_client 统一处理为 A3001 或降级。
    """

    async def transcribe(self, audio_bytes: bytes, fmt: str) -> str:
        """转写音频为文本。

        Args:
            audio_bytes: 音频文件二进制内容（mp3/wav/m4a，faster-whisper 自动解码）。
            fmt: 音频格式（保留入参兼容 Provider 抽象签名，本地解码自动识别）。

        Returns:
            识别出的文本字符串。

        Raises:
            RuntimeError: 依赖缺失、模型加载失败或转写异常。
        """
        import asyncio

        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise RuntimeError(f"Whisper 依赖未安装: {e}")

        model = getattr(self, "_model", None)
        if model is None:
            with _MODEL_LOCK:
                # 双重检查：锁内再次确认，避免并发首次调用重复加载模型
                model = getattr(self, "_model", None)
                if model is None:
                    try:
                        model = WhisperModel("tiny", device="cpu", compute_type="int8")
                    except Exception as e:
                        raise RuntimeError(f"Whisper 模型加载失败: {e}")
                    self._model = model

        def _run() -> str:
            try:
                segments, _ = model.transcribe(audio_bytes)
                return "".join(seg.text for seg in segments)
            except Exception as e:
                raise RuntimeError(f"Whisper 转写失败: {e}")

        return await asyncio.to_thread(_run)

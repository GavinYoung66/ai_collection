"""Whisper ASR Provider 实现（本地 faster-whisper 模型转写）。"""
import asyncio

from app.services.asr_providers.base import ASRProvider

# T65：asyncio.Lock 门控懒加载（事件循环单线程内争用可见；threading.Lock 在
# 纯协程上下文中永不争用，形同虚设）。加载体移入 to_thread，不阻塞事件循环。
_MODEL_LOCK = asyncio.Lock()


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
            async with _MODEL_LOCK:
                # T65：双重检查 + 加载移入线程池——模型加载耗时数秒，
                # MUST NOT 在事件循环线程内同步执行（期间 /health 与并发请求全部阻塞）
                model = getattr(self, "_model", None)
                if model is None:
                    try:
                        model = await asyncio.to_thread(
                            WhisperModel, "tiny", device="cpu", compute_type="int8"
                        )
                    except Exception as e:
                        raise RuntimeError(f"Whisper 模型加载失败: {e}")
                    self._model = model

        def _run() -> str:
            # model 在上方任一 None 分支已抛 RuntimeError，到达此处必非 None；
            # assert 供 mypy 收窄 union-attr（运行时不可达）
            assert model is not None
            try:
                segments, _ = model.transcribe(audio_bytes)
                return "".join(seg.text for seg in segments)
            except Exception as e:
                raise RuntimeError(f"Whisper 转写失败: {e}")

        return await asyncio.to_thread(_run)

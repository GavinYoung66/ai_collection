"""语音能力包：ASR 语音识别 + TTS 语音合成。"""
from common.speech.asr_provider import ASRProvider, ASRResult, asr_provider
from common.speech.tts_provider import TTSProvider, tts_provider

__all__ = ["ASRProvider", "ASRResult", "asr_provider", "TTSProvider", "tts_provider"]

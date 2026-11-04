"""T38：common.speech 真实 ASR（阿里云 NLS WebSocket）集成测试。

启用条件（两者同时满足，否则 skip）：
- `COMMON_ASR_TESTS=1`；
- `ASR_APP_KEY` / `ASR_ACCESS_TOKEN` 为非占位值（占位值在 dev 下只会走 mock
  分支，不构成真实依赖路径，见 ``is_placeholder_secret``）。

音频为本地合成的 16kHz/16bit/单声道正弦波 PCM（无真实人声，识别文本可为空），
免依赖真实录音资产；用例验证的是真实上行（WebSocket 分片发送 + stop 指令）与
真实下行（服务端回包解析）全链路可执行，而非 mock 分支的结构断言。
"""
from __future__ import annotations

import math
import os
import struct

import pytest

from common.config.settings import settings
from common.llm.reliability import is_placeholder_secret

_SAMPLE_RATE = 16000
_DURATION_SECONDS = 1
_TONE_HZ = 440


def _synthetic_pcm() -> bytes:
    """合成 1s 440Hz 正弦波 PCM（16kHz/16bit/mono）。"""
    frames = []
    for i in range(_SAMPLE_RATE * _DURATION_SECONDS):
        value = int(8000 * math.sin(2 * math.pi * _TONE_HZ * i / _SAMPLE_RATE))
        frames.append(struct.pack("<h", value))
    return b"".join(frames)


@pytest.fixture()
def real_asr_required():
    """真实 ASR 集成开关：环境开关 + 非占位凭证双重门槛，否则 skip。"""
    if os.getenv("COMMON_ASR_TESTS") != "1":
        pytest.skip("COMMON_ASR_TESTS != 1，跳过需要真实 ASR 服务的集成用例")
    if is_placeholder_secret(settings.ASR_APP_KEY) or is_placeholder_secret(
        settings.ASR_ACCESS_TOKEN
    ):
        pytest.skip("ASR_APP_KEY/ASR_ACCESS_TOKEN 为占位值，跳过真实 ASR 集成用例")


async def test_real_asr_recognize_roundtrip(real_asr_required):
    """真实 NLS WebSocket 全链路：上行分片 + stop 指令 + 下行回包解析。

    断言返回 ``ASRResult`` 且字段类型/取值域合法；正弦波不含人声，识别文本
    允许为空字符串，但置信度必须落在 [0, 1]。
    """
    from common.speech.asr_provider import ASRProvider, ASRResult

    provider = ASRProvider()
    result = await provider.recognize(_synthetic_pcm())
    assert isinstance(result, ASRResult)
    assert isinstance(result.text, str)
    assert 0.0 <= result.confidence <= 1.0

"""ASR 通话开场白规则裁剪单元测试（V4：design.md D6/D8 落地 / tasks 7.5）。

覆盖：
- 开场白规则裁剪开启：问候语 + 身份确认 + 公司名模式被裁剪至首个业务内容起点
- 开关关闭：转写文本原样返回（=V3 行为）
- 无开场白文本不误删
- 裁剪后剩余过短（纯开场白）保底不裁
- 规则缺失/解析失败 → 按开关关闭处理
"""
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services.asr_client import ASRClient
from app.settings import settings

_RULES_PATH = Path(__file__).resolve().parent.parent / "conf" / "asr_opening_rules.yaml"

# 主场景：问候 + 身份确认 + 公司名 → 裁剪后仅剩业务内容
_MAIN_TEXT = "喂，你好，请问是李女士吗？我们是XX银行公司法务部，您有一笔逾期账单需要处理"
_MAIN_TRIMMED = "您有一笔逾期账单需要处理"


def _real_rules() -> dict:
    return ASRClient.load_opening_rules(_RULES_PATH)


class TestTrimOpeningText:
    def test_trim_main_scenario(self):
        """主场景：问候语/身份确认/公司名连续消费，裁剪至业务内容起点。"""
        assert ASRClient.trim_opening_text(_MAIN_TEXT, _real_rules()) == _MAIN_TRIMMED

    def test_trim_no_opening_not_trimmed(self):
        """无开场白文本不误删（offset==0 → 原样返回）。"""
        text = "您有一笔逾期账单需要处理"
        assert ASRClient.trim_opening_text(text, _real_rules()) == text

    def test_trim_pure_opening_keeps_original(self):
        """纯开场白（裁剪后剩余 < trim_min_remain_len）→ 保底不裁。"""
        text = "喂，你好，请问是李女士吗？"
        assert ASRClient.trim_opening_text(text, _real_rules()) == text

    def test_trim_empty_rules_not_trimmed(self):
        """规则缺失/无 patterns → 按开关关闭处理，不裁剪。"""
        assert ASRClient.trim_opening_text(_MAIN_TEXT, {}) == _MAIN_TEXT

    def test_trim_rules_missing_file_returns_empty(self):
        """配置文件缺失 → load_opening_rules 返回空 dict（不抛异常）。"""
        assert ASRClient.load_opening_rules(Path("/nonexistent/rules.yaml")) == {}

    def test_trim_real_rules_file_loads(self):
        """真实规则文件可加载且含 patterns。"""
        rules = _real_rules()
        assert rules.get("patterns")
        assert rules.get("leading_max_len", 60) > 0


class TestTranscribeTrimGate:
    def _make_provider(self, text: str):
        class MockProvider:
            async def transcribe(self, audio_bytes, fmt):
                return text

        return MockProvider()

    def _make_client(self, monkeypatch, provider):
        monkeypatch.setattr(
            "app.services.asr_client.ASRFactory.create", lambda: provider
        )
        client = ASRClient(redis=None)
        client._is_degraded = AsyncMock(return_value=False)
        return client

    @pytest.mark.asyncio
    async def test_transcribe_trim_enabled(self, monkeypatch):
        """开关开启 → 转写文本拼接后执行开场白裁剪。"""
        monkeypatch.setattr(settings, "KB_ASR_OPENING_TRIM_ENABLED", True)
        client = self._make_client(monkeypatch, self._make_provider(_MAIN_TEXT))
        result = await client.transcribe(b"fake-audio", "test.mp3")
        assert result == _MAIN_TRIMMED

    @pytest.mark.asyncio
    async def test_transcribe_trim_disabled_v3_behavior(self, monkeypatch):
        """开关关闭 → 转写文本原样返回（=V3 行为，不裁剪）。"""
        monkeypatch.setattr(settings, "KB_ASR_OPENING_TRIM_ENABLED", False)
        client = self._make_client(monkeypatch, self._make_provider(_MAIN_TEXT))
        result = await client.transcribe(b"fake-audio", "test.mp3")
        assert result == _MAIN_TEXT

    @pytest.mark.asyncio
    async def test_transcribe_trim_no_greeting_audio_kept(self, monkeypatch):
        """无开场白音频 → 不误删。"""
        monkeypatch.setattr(settings, "KB_ASR_OPENING_TRIM_ENABLED", True)
        business = "您的逾期账单已进入催收流程，请联系客服协商还款方案"
        client = self._make_client(monkeypatch, self._make_provider(business))
        result = await client.transcribe(b"fake-audio", "test.mp3")
        assert result == business

    @pytest.mark.asyncio
    async def test_transcribe_trim_short_pure_opening_kept(self, monkeypatch):
        """纯开场白（裁剪后过短）→ 保底不裁，返回完整文本。"""
        monkeypatch.setattr(settings, "KB_ASR_OPENING_TRIM_ENABLED", True)
        short = "喂，你好，请问是李女士吗？"
        client = self._make_client(monkeypatch, self._make_provider(short))
        result = await client.transcribe(b"fake-audio", "test.mp3")
        assert result == short

    @pytest.mark.asyncio
    async def test_transcribe_rules_missing_keeps_text(self, monkeypatch):
        """规则文件缺失 → 空 rules → 不裁剪（不抛异常）。"""
        monkeypatch.setattr(settings, "KB_ASR_OPENING_TRIM_ENABLED", True)
        monkeypatch.setattr(ASRClient, "load_opening_rules", staticmethod(lambda *a, **k: {}))
        client = self._make_client(monkeypatch, self._make_provider(_MAIN_TEXT))
        result = await client.transcribe(b"fake-audio", "test.mp3")
        assert result == _MAIN_TEXT

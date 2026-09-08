"""V2：USE_MOCK_LLM 装配路径断言。"""
from __future__ import annotations

import pytest

from app.api.deps import reset_llm_provider
from app.config import settings
from app.llm.provider import ComplaintLLMProvider
from app.llm.real_transport import RealLLMTransport, _is_placeholder


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_llm_provider()
    yield
    reset_llm_provider()


def test_mock_path_when_flag_true(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", True)
    p = ComplaintLLMProvider()
    assert p.using_mock is True
    assert p._get_transport().__class__.__name__ == "MockLLMProvider"


def test_real_path_when_flag_false(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    p = ComplaintLLMProvider()
    assert p.using_mock is False
    assert isinstance(p._get_transport(), RealLLMTransport)


@pytest.mark.asyncio
async def test_real_transport_rejects_placeholder_key(monkeypatch):
    monkeypatch.setattr(settings, "USE_MOCK_LLM", False)
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_DASHSCOPE_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_API_KEY", "change_me")
    t = RealLLMTransport()
    with pytest.raises(RuntimeError, match="real LLM key missing"):
        await t.invoke("请给出判责 JSON", agent_role="verdict")


@pytest.mark.asyncio
async def test_deepseek_falls_back_to_qwen_when_only_dashscope(monkeypatch):
    monkeypatch.setattr(settings, "LLM_DEEPSEEK_API_KEY", "change_me")
    monkeypatch.setattr(settings, "LLM_DASHSCOPE_API_KEY", "sk-test-dashscope-key")
    monkeypatch.setattr(settings, "LLM_API_KEY", "change_me")
    t = RealLLMTransport()
    model, provider, api_key, _base = t._resolve_model_and_creds("verdict")
    assert model == "qwen-plus"
    assert provider == "dashscope"
    assert api_key.startswith("sk-test")


def test_placeholder_helper():
    assert _is_placeholder("change_me")
    assert not _is_placeholder("sk-live-not-real-but-nonempty")

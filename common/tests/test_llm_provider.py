"""common.llm.provider 走 transport 的单测。"""
from unittest.mock import AsyncMock, patch

import pytest

from common.llm import provider as provider_mod


@pytest.mark.asyncio
async def test_chat_uses_post_chat_completions(monkeypatch):
    monkeypatch.setattr(provider_mod.settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(provider_mod.settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(provider_mod.settings, "LLM_TIMEOUT", 10)

    fake_body = {"choices": [{"message": {"content": "  hi  "}}]}
    with patch(
        "common.llm.provider.post_chat_completions",
        new_callable=AsyncMock,
        return_value=fake_body,
    ) as mock_post:
        text = await provider_mod.chat([{"role": "user", "content": "x"}])

    assert text == "hi"
    mock_post.assert_awaited_once()
    kwargs = mock_post.await_args.kwargs
    assert kwargs["api_key"] == "sk-test"
    assert kwargs["base_url"].endswith("/compatible-mode/v1")
    assert kwargs["model"] == "qwen-plus"


@pytest.mark.asyncio
async def test_chat_json_sets_json_object(monkeypatch):
    monkeypatch.setattr(provider_mod.settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(provider_mod.settings, "LLM_MODEL", "qwen-plus")
    monkeypatch.setattr(provider_mod.settings, "LLM_TIMEOUT", 10)

    fake_body = {"choices": [{"message": {"content": "{\"a\":1}"}}]}
    with patch(
        "common.llm.provider.post_chat_completions",
        new_callable=AsyncMock,
        return_value=fake_body,
    ) as mock_post:
        text = await provider_mod.chat_json([{"role": "user", "content": "x"}])

    assert text == '{"a":1}'
    assert mock_post.await_args.kwargs["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# per-key 熔断（替代全局 @llm_breaker）
# ---------------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _isolate_service():
    from common.llm import reliability as reliability_mod

    reliability_mod.set_default_service("csmp")
    provider_mod._per_key_breakers.clear()
    yield
    reliability_mod.set_default_service(None)


@pytest.mark.asyncio
async def test_chat_circuit_open_rejects_without_post(monkeypatch):
    monkeypatch.setattr(provider_mod.settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(provider_mod.settings, "LLM_MODEL", "qwen-plus")
    breaker = provider_mod._per_key_breakers.get_or_create("csmp:llm_degraded:qwen-plus")
    for _ in range(3):
        await breaker.record_failure()

    with patch(
        "common.llm.provider.post_chat_completions", new_callable=AsyncMock
    ) as mock_post:
        with pytest.raises(RuntimeError, match="circuit open"):
            await provider_mod.chat([{"role": "user", "content": "x"}])

    mock_post.assert_not_awaited()  # 熔断拒绝：不触达 HTTP


@pytest.mark.asyncio
async def test_chat_per_model_isolation(monkeypatch):
    monkeypatch.setattr(provider_mod.settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(provider_mod.settings, "LLM_MODEL", "qwen-plus")
    breaker = provider_mod._per_key_breakers.get_or_create("csmp:llm_degraded:qwen-plus")
    for _ in range(3):
        await breaker.record_failure()

    fake_body = {"choices": [{"message": {"content": "hi"}}]}
    with patch(
        "common.llm.provider.post_chat_completions",
        new_callable=AsyncMock,
        return_value=fake_body,
    ):
        text = await provider_mod.chat(
            [{"role": "user", "content": "x"}], model="deepseek-chat"
        )

    assert text == "hi"  # qwen-plus 熔断不影响 deepseek-chat

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

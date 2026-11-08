"""T106：LLM 双熔断注册表收敛——provider 与编排器共享同一实例。

spec：platform/resilience-primitives「同一 key 共享熔断状态」——
同一模型 key 分别经 provider（chat）与可靠性编排器（invoke_llm）调用，
两入口读写同一熔断实例，状态不分裂（不出现一边 open 一边 closed）。
"""
from unittest.mock import AsyncMock, patch

import pytest

from common.llm import provider as provider_mod
from common.llm import reliability as reliability_mod
from common.llm.reliability.orchestrator import _llm_breakers
from common.resilience.breaker import make_llm_breaker_key

MODEL = "qwen-plus"


@pytest.fixture(autouse=True)
def _isolate_service():
    reliability_mod.set_default_service("csmp")
    _llm_breakers.clear()
    yield
    _llm_breakers.clear()
    reliability_mod.set_default_service(None)


def _key() -> str:
    return make_llm_breaker_key("csmp", MODEL)


def test_breaker_for_returns_orchestrator_registry_instance():
    """provider 侧 _breaker_for 与编排器注册表 get_or_create 返回同一实例。"""
    breaker_via_provider = provider_mod._breaker_for(MODEL)
    breaker_via_orchestrator = _llm_breakers.get_or_create(_key())
    assert breaker_via_provider is breaker_via_orchestrator


@pytest.mark.asyncio
async def test_open_via_orchestrator_rejects_provider_chat(monkeypatch):
    """编排器侧打开熔断 → provider chat 入口同 key 被拒，不触达 HTTP。"""
    monkeypatch.setattr(provider_mod.settings, "LLM_API_KEY", "sk-test")
    monkeypatch.setattr(provider_mod.settings, "LLM_MODEL", MODEL)
    breaker = _llm_breakers.get_or_create(_key())
    for _ in range(3):
        await breaker.record_failure()
    assert breaker.state == "open"

    with patch(
        "common.llm.provider.post_chat_completions", new_callable=AsyncMock
    ) as mock_post:
        with pytest.raises(RuntimeError, match="circuit open"):
            await provider_mod.chat([{"role": "user", "content": "x"}])

    mock_post.assert_not_awaited()


async def test_open_via_provider_visible_to_orchestrator_entry():
    """provider 侧打开熔断 → 编排器注册表同 key 为同一实例且状态 open。"""
    breaker_via_provider = provider_mod._breaker_for(MODEL)
    for _ in range(3):
        await breaker_via_provider.record_failure()

    breaker_via_orchestrator = _llm_breakers.get(_key())
    assert breaker_via_orchestrator is breaker_via_provider
    assert breaker_via_orchestrator.state == "open"


def test_provider_module_has_no_second_registry():
    """形态守护：provider 模块不再自建第二套熔断注册表。"""
    assert not hasattr(provider_mod, "_per_key_breakers")
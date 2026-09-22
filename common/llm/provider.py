"""LLM 统一调用入口：DashScope 兼容模式，带重试 + per-key 熔断。

【遗留薄封装 / PLAT-01】`chat` / `chat_json` 仅兼容旧调用方；新代码优先
`common.llm.transport` + `common.llm.reliability.invoke_llm`。
【PLAT-02】多实例降级以 Redis degrade 标志为准。
【批次2】`@llm_breaker` 全局单实例已替换为 per-key breaker
（key=`{svc}:llm_degraded:{model}`），一个模型/服务的熔断不再影响其他
服务与模型，消灭 `csmp:llm_degraded=1` 全局单 key。

底层经 common.llm.transport.post_chat_completions（不再使用 openai SDK）。
"""
from __future__ import annotations

from common.config.settings import settings
from common.llm.reliability import (
    call_with_retry,
    is_placeholder_secret,
    require_real_secret,
    resolve_service_name,
)
from common.resilience.breaker import BreakerRegistry, make_llm_breaker_key
from common.llm.transport import message_content_from_completions, post_chat_completions

_DASHSCOPE_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_per_key_breakers = BreakerRegistry(
    fail_max=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
    reset_timeout=settings.LLM_CIRCUIT_BREAKER_RECOVERY,
)


def _resolve_api_key() -> str:
    """优先 LLM_API_KEY，其次 DASHSCOPE_API_KEY。"""
    key = (settings.LLM_API_KEY or "").strip()
    if key and not is_placeholder_secret(key):
        return key
    return (settings.DASHSCOPE_API_KEY or "").strip()


def _breaker_for(model: str | None):
    """per-service-per-model 熔断器；无全局单 key。"""
    return _per_key_breakers.get_or_create(
        make_llm_breaker_key(resolve_service_name(), model or settings.LLM_MODEL)
    )


async def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.1,
    response_format: dict | None = None,
) -> str:
    breaker = _breaker_for(model)
    if not await breaker.before_call():
        raise RuntimeError(f"LLM circuit open for {breaker.key}")
    try:
        content = await call_with_retry(
            lambda: _chat_once(
                messages, model=model, temperature=temperature, response_format=response_format
            ),
            max_attempts=3,
            delay=1.0,
        )
    except Exception as e:
        await breaker.record_failure(e)
        raise
    await breaker.record_success()
    return content


async def _chat_once(
    messages: list[dict],
    *,
    model: str | None,
    temperature: float,
    response_format: dict | None,
) -> str:
    api_key = require_real_secret(_resolve_api_key(), label="LLM_API_KEY / DASHSCOPE_API_KEY")
    body = await post_chat_completions(
        base_url=_DASHSCOPE_COMPAT_BASE,
        api_key=api_key,
        model=model or settings.LLM_MODEL,
        messages=messages,
        timeout=float(settings.LLM_TIMEOUT),
        temperature=temperature,
        response_format=response_format,
    )
    content = message_content_from_completions(body)
    if not content:
        raise RuntimeError("LLM returned empty content")
    return content


async def chat_json(messages: list[dict], model: str | None = None) -> str:
    """强制 JSON 输出。"""
    return await chat(messages, model=model, response_format={"type": "json_object"})

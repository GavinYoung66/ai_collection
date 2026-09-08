"""LLM 统一调用入口：DashScope 兼容模式，带重试 + 熔断。

【遗留薄封装 / PLAT-01】`chat` / `chat_json` 仅兼容旧调用方；新代码优先
`common.llm.transport` + `common.llm.reliability`（或服务内 RealLLMTransport）。
【PLAT-02】多实例降级以 Redis degrade 标志为准；本模块 `@llm_breaker` 仅为单进程辅助。

底层经 common.llm.transport.post_chat_completions（不再使用 openai SDK）。
"""
from __future__ import annotations

from common.config.settings import settings
from common.llm.circuit_breaker import llm_breaker
from common.llm.reliability import is_placeholder_secret, require_real_secret
from common.llm.transport import message_content_from_completions, post_chat_completions
from common.utils.retry import retry

_DASHSCOPE_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"


def _resolve_api_key() -> str:
    """优先 LLM_API_KEY，其次 DASHSCOPE_API_KEY。"""
    key = (settings.LLM_API_KEY or "").strip()
    if key and not is_placeholder_secret(key):
        return key
    return (settings.DASHSCOPE_API_KEY or "").strip()


@llm_breaker
@retry(max_attempts=3, delay=1.0, exceptions=(Exception,))
async def chat(
    messages: list[dict],
    model: str | None = None,
    temperature: float = 0.1,
    response_format: dict | None = None,
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


@llm_breaker
@retry(max_attempts=3, delay=1.0, exceptions=(Exception,))
async def chat_json(messages: list[dict], model: str | None = None) -> str:
    """强制 JSON 输出。"""
    return await chat(messages, model=model, response_format={"type": "json_object"})

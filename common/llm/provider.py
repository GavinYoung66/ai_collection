"""LLM 统一调用入口：DashScope qwen-plus，带重试 + 熔断。"""
import structlog
from openai import AsyncOpenAI

from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.llm.circuit_breaker import llm_breaker
from common.utils.retry import retry

logger = structlog.get_logger()

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
    return _client


@llm_breaker
@retry(max_attempts=3, delay=1.0, exceptions=(Exception,))
async def chat(messages: list[dict], model: str | None = None, temperature: float = 0.1, response_format: dict | None = None) -> str:
    client = _get_client()
    resp = await client.chat.completions.create(
        model=model or settings.LLM_MODEL,
        messages=messages,
        temperature=temperature,
        response_format=response_format,
    )
    return resp.choices[0].message.content


@llm_breaker
@retry(max_attempts=3, delay=1.0, exceptions=(Exception,))
async def chat_json(messages: list[dict], model: str | None = None) -> str:
    """强制 JSON 输出。"""
    return await chat(messages, model=model, response_format={"type": "json_object"})

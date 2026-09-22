"""DeepSeek OpenAI 兼容 transport（无 retry/熔断）。"""
import httpx

from app.settings import settings
from common.llm.reliability import require_real_secret
from common.llm.transport import message_content_from_completions, post_chat_completions


class DeepSeekProvider:
    """DeepSeek /v1/chat/completions；只发 HTTP + 解析。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def __call__(
        self, model: str, messages: list[dict], timeout_seconds: float
    ) -> str:
        api_key = require_real_secret(
            settings.deepseek_api_key_resolved, label="DEEPSEEK_API_KEY"
        )
        client = await self._get_client()
        body = await post_chat_completions(
            base_url=settings.DEEPSEEK_BASE_URL,
            api_key=api_key,
            model=model,
            messages=messages,
            timeout=timeout_seconds,
            temperature=0.3,
            client=client,
        )
        text = message_content_from_completions(body)
        if not text:
            raise ValueError("empty LLM content")
        return text

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

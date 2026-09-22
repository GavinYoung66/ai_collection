"""本地 OpenAI 兼容 transport（无 retry/熔断）。"""
import httpx

from app.settings import settings
from common.llm.transport import message_content_from_completions, post_chat_completions


class LocalOpenAIProvider:
    """LLM_LOCAL_BASE_URL + LLM_LOCAL_API_KEY；只发 HTTP + 解析。"""

    def __init__(self) -> None:
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def __call__(
        self, model: str, messages: list[dict], timeout_seconds: float
    ) -> str:
        if not settings.LLM_LOCAL_BASE_URL:
            raise RuntimeError("LLM_LOCAL_BASE_URL not configured")
        client = await self._get_client()
        body = await post_chat_completions(
            base_url=settings.LLM_LOCAL_BASE_URL,
            api_key=settings.LLM_LOCAL_API_KEY or "",
            model=model,
            messages=messages,
            timeout=timeout_seconds,
            client=client,
        )
        text = message_content_from_completions(body)
        if not text:
            raise ValueError("empty LLM content")
        return text

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

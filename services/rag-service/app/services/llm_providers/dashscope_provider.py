"""DashScope 原生 text-generation transport（无 retry/熔断）。"""
import httpx

from app.settings import settings
from common.llm.reliability import require_real_secret
from common.llm.transport import post_json


class DashScopeProvider:
    """阿里云 DashScope 原生 endpoint；只发 HTTP + 解析 output.text。"""

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
            settings.DASHSCOPE_API_KEY or settings.LLM_API_KEY,
            label="DASHSCOPE_API_KEY",
        )
        client = await self._get_client()
        body = await post_json(
            url=settings.LLM_DASHSCOPE_ENDPOINT,
            api_key=api_key,
            payload={"model": model, "input": {"messages": messages}},
            timeout=timeout_seconds,
            client=client,
            error_label="DashScope",
        )
        text = body["output"]["text"]
        if not text:
            raise ValueError("empty LLM content")
        return text

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

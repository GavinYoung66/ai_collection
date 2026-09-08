"""Local OpenAI 兼容 LLM Provider（V5，design D8）。"""
import asyncio
import httpx
import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.services.llm_providers.base import LLMAnswer, LLMProvider, build_answer_prompt
from app.settings import settings
from common.llm.transport import message_content_from_completions, post_chat_completions

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:llm:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class LocalOpenAIProvider(LLMProvider):
    """本地 OpenAI 兼容服务回答实现（LLM_LOCAL_BASE_URL/LLM_LOCAL_API_KEY/LLM_MODEL）。

    - 未配置基址 → 按不可用降级（raise RuntimeError，llm_service 捕获返回 answer=None）；
    - 连续 3 次失败写入 Redis `rag:llm:degraded`（TTL 60s），窗口内短路。
    """

    def __init__(self, redis: Redis | None = None):
        self._redis = redis
        self._client: httpx.AsyncClient | None = None
        self._failure_count = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def _is_degraded(self) -> bool:
        if self._redis is None:
            return False
        try:
            return await self._redis.exists(_DEGRADED_KEY) > 0
        except (RedisError, OSError) as e:
            logger.warning("llm_degraded_probe_failed", error=str(e))
            return False

    async def _on_success(self) -> None:
        self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= _FAILURE_THRESHOLD:
            if self._redis is not None:
                await self._redis.setex(_DEGRADED_KEY, _CIRCUIT_TTL, "1")
            logger.error("llm_circuit_break_open", failures=self._failure_count, error=str(error))

    async def generate(
        self, context: list[dict], query: str, timeout: float
    ) -> LLMAnswer:
        if not settings.LLM_LOCAL_BASE_URL:
            raise RuntimeError("LLM_LOCAL_BASE_URL not configured")
        if await self._is_degraded():
            raise RuntimeError("LLM service is in circuit-break state")

        system, user = build_answer_prompt(context, query)
        client = await self._get_client()
        for attempt in range(3):
            try:
                body = await post_chat_completions(
                    base_url=settings.LLM_LOCAL_BASE_URL,
                    api_key=settings.LLM_LOCAL_API_KEY or "",
                    model=settings.LLM_MODEL,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    timeout=timeout,
                    client=client,
                )
                text = message_content_from_completions(body)
                if not text:
                    raise ValueError("empty LLM content")
                await self._on_success()
                return LLMAnswer(
                    text=text,
                    citations=sorted({c["doc_id"] for c in context if c.get("doc_id")}),
                )
            except (httpx.HTTPError, KeyError, ValueError, IndexError, RuntimeError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                else:
                    await self._on_failure(e)
                    raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

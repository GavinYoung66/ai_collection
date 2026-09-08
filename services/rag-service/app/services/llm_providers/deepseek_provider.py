"""DeepSeek OpenAI 兼容 LLM Provider（V5）。"""
import httpx
import structlog
from redis.asyncio import Redis

from app.services.llm_providers.base import LLMAnswer, LLMProvider, build_answer_prompt
from app.settings import settings
from common.llm.reliability import RedisDegradeTracker, call_with_retry, require_real_secret
from common.llm.transport import message_content_from_completions, post_chat_completions

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:llm:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class DeepSeekProvider(LLMProvider):
    """DeepSeek Chat Completions（OpenAI 兼容 /v1/chat/completions）。

    API Key 读取 settings.deepseek_api_key_resolved（兼容 DEEPSEEK_API_KEY / DeepSeek_API_KEY）。
    重试/降级经 common.llm.reliability。
    """

    def __init__(self, redis: Redis | None = None):
        self._redis = redis
        self._client: httpx.AsyncClient | None = None
        self._degrade = RedisDegradeTracker(
            redis,
            key=_DEGRADED_KEY,
            failure_threshold=_FAILURE_THRESHOLD,
            ttl_seconds=_CIRCUIT_TTL,
        )

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient()
        return self._client

    async def generate(
        self, context: list[dict], query: str, timeout: float
    ) -> LLMAnswer:
        api_key = require_real_secret(
            settings.deepseek_api_key_resolved, label="DEEPSEEK_API_KEY"
        )
        if await self._degrade.is_degraded():
            raise RuntimeError("LLM service is in circuit-break state")

        system, user = build_answer_prompt(context, query)
        client = await self._get_client()

        async def _once() -> LLMAnswer:
            body = await post_chat_completions(
                base_url=settings.DEEPSEEK_BASE_URL,
                api_key=api_key,
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                timeout=timeout,
                temperature=0.3,
                client=client,
            )
            text = message_content_from_completions(body)
            if not text:
                raise ValueError("empty LLM content")
            return LLMAnswer(
                text=text,
                citations=sorted({c["doc_id"] for c in context if c.get("doc_id")}),
            )

        try:
            answer = await call_with_retry(
                _once,
                max_attempts=3,
                delay=1.0,
                exceptions=(httpx.HTTPError, KeyError, ValueError, IndexError, RuntimeError),
            )
            await self._degrade.on_success()
            return answer
        except (httpx.HTTPError, KeyError, ValueError, IndexError, RuntimeError) as e:
            await self._degrade.on_failure(e)
            logger.error("llm_circuit_break_open", error=str(e))
            raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

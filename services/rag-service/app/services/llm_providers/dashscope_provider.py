"""DashScope qwen LLM Provider（经 common.llm.reliability + transport）。"""
import httpx
import structlog
from redis.asyncio import Redis

from app.services.llm_providers.base import LLMAnswer, LLMProvider, build_answer_prompt
from app.settings import settings
from common.llm.reliability import RedisDegradeTracker, call_with_retry, require_real_secret
from common.llm.transport import post_json

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:llm:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60


class DashScopeProvider(LLMProvider):
    """阿里云 DashScope qwen 回答实现（text-generation 原生格式）。

    重试/降级经 common.llm.reliability；Key 占位 fail-closed。
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
            settings.DASHSCOPE_API_KEY or settings.LLM_API_KEY,
            label="DASHSCOPE_API_KEY",
        )
        if await self._degrade.is_degraded():
            raise RuntimeError("LLM service is in circuit-break state")

        system, user = build_answer_prompt(context, query)
        payload = {
            "model": settings.LLM_MODEL,
            "input": {
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ]
            },
        }
        client = await self._get_client()

        async def _once() -> LLMAnswer:
            body = await post_json(
                url=settings.LLM_DASHSCOPE_ENDPOINT,
                api_key=api_key,
                payload=payload,
                timeout=timeout,
                client=client,
                error_label="DashScope",
            )
            text = body["output"]["text"]
            return LLMAnswer(
                text=text,
                citations=sorted({c["doc_id"] for c in context if c.get("doc_id")}),
            )

        try:
            answer = await call_with_retry(
                _once,
                max_attempts=3,
                delay=1.0,
                exceptions=(httpx.HTTPError, KeyError, ValueError, RuntimeError),
            )
            await self._degrade.on_success()
            return answer
        except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as e:
            await self._degrade.on_failure(e)
            logger.error("llm_circuit_break_open", error=str(e))
            raise

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

"""Query 改写服务：HTTP 调用 DashScope 文本生成接口，含熔断逻辑。

- 未配置 DASHSCOPE_API_KEY 或 Redis 熔断键存在时，直接降级返回原 query。
- 3 次指数退避重试，连续 3 次失败写入 Redis `rag:rewrite:degraded`（TTL 60s）。
- LLM 返回非严格 JSON 或 confidence < 0.6 时降级，rewritten_query 退回原 query。
"""
import asyncio
import json

import httpx
import structlog
from redis.asyncio import Redis

from app.models.search import RewriteResult
from app.settings import settings
from common.llm.transport import post_json

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:rewrite:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60
_CONFIDENCE_THRESHOLD = 0.6

_SYSTEM_PROMPT = (
    "你是催收知识库 Query 纠错专家，只输出严格 JSON。要求："
    "1. 识别错别字/口语缩写/术语归一化；"
    "2. 扩展近义术语；"
    '3. 输出 {"rewritten_query": str, "confidence": float 0-1}。'
    "confidence 表示改写正确性把握。"
)


class RewriteService:
    """Query 改写服务，调用 DashScope 文本生成接口做纠错/术语归一/扩展。

    包含 3 次指数退避重试与熔断逻辑（连续 3 次失败写入 Redis
    `rag:rewrite:degraded`，TTL 60s）；任何降级路径均返回原 query，
    不向调用方抛异常。
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
        return await self._redis.exists(_DEGRADED_KEY) > 0

    async def _on_success(self) -> None:
        self._failure_count = 0

    async def _on_failure(self, error: Exception) -> None:
        self._failure_count += 1
        if self._failure_count >= _FAILURE_THRESHOLD:
            await self._set_degraded()
            logger.error(
                "rewrite_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
            )

    async def _set_degraded(self, ttl: int = _CIRCUIT_TTL) -> None:
        if self._redis is not None:
            await self._redis.setex(_DEGRADED_KEY, ttl, "1")

    def _build_user_content(self, query: str, history: list[str] | None) -> str:
        """构造 user 内容：原始 query + 可选最近 N 轮历史上下文摘要。"""
        if not history:
            return query
        recent = history[-settings.SESSION_REWRITE_LOOKBACK:]
        summary = "\n".join(f"- {h}" for h in recent)
        return (
            f"{query}\n\nContext（最近 {len(recent)} 轮历史）：\n{summary}"
        )

    @staticmethod
    def _parse_rewrite_json(text: str) -> dict | None:
        """解析 LLM 输出的严格 JSON，失败返回 None。

        兼容被 ```json 代码块包裹的输出；校验 rewritten_query 为 str、
        confidence 为数值并夹紧到 [0, 1]。
        """
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            # 去掉首行 ```json / ``` 与末尾 ```
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()
        try:
            data = json.loads(cleaned)
        except (json.JSONDecodeError, TypeError):
            return None
        if not isinstance(data, dict):
            return None
        rewritten = data.get("rewritten_query")
        confidence = data.get("confidence")
        if not isinstance(rewritten, str) or not rewritten:
            return None
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
            return None
        return {
            "rewritten_query": rewritten,
            "confidence": max(0.0, min(1.0, float(confidence))),
        }

    async def rewrite(self, query: str, history: list[str] | None = None) -> RewriteResult:
        """对用户 query 做 LLM 纠错/术语归一/扩展改写。

        Args:
            query: 原始用户查询。
            history: 可选历史轮次文本列表，取最近 SESSION_REWRITE_LOOKBACK 轮作为上下文。

        Returns:
            RewriteResult：degraded=True 时 rewritten_query 退回原 query。
        """
        # 未配置 API Key → 直接降级
        if not settings.DASHSCOPE_API_KEY:
            logger.warning("rewrite_disabled_no_api_key")
            return RewriteResult(rewritten_query=query, confidence=0.0, degraded=True)

        # 熔断短路 → 直接降级
        if await self._is_degraded():
            logger.info("rewrite_circuit_break_short_circuit")
            return RewriteResult(rewritten_query=query, confidence=0.0, degraded=True)

        payload = {
            "model": settings.LLM_MODEL,
            "input": {
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": self._build_user_content(query, history)},
                ]
            },
        }

        client = await self._get_client()
        for attempt in range(3):
            try:
                body = await post_json(
                    url=settings.LLM_DASHSCOPE_ENDPOINT,
                    api_key=settings.DASHSCOPE_API_KEY,
                    payload=payload,
                    timeout=float(settings.LLM_TIMEOUT),
                    client=client,
                    error_label="DashScopeRewrite",
                )
                await self._on_success()
                text = body["output"]["text"]
                parsed = self._parse_rewrite_json(text)
                if parsed is None:
                    # LLM 输出非严格 JSON → 降级返回原 query
                    logger.warning("rewrite_parse_failed", output=text)
                    return RewriteResult(
                        rewritten_query=query, confidence=0.0, degraded=True
                    )
                confidence = parsed["confidence"]
                # confidence 低于阈值 → 强制降级，退回原 query
                if confidence < _CONFIDENCE_THRESHOLD:
                    logger.info(
                        "rewrite_low_confidence",
                        confidence=confidence,
                        rewritten=parsed["rewritten_query"],
                    )
                    return RewriteResult(
                        rewritten_query=query,
                        confidence=confidence,
                        degraded=True,
                    )
                return RewriteResult(
                    rewritten_query=parsed["rewritten_query"],
                    confidence=confidence,
                    degraded=False,
                )
            except (httpx.HTTPError, KeyError, ValueError, RuntimeError) as e:
                if attempt < 2:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                # 3 次重试耗尽 → 计入熔断并降级返回（不向调用方抛异常）
                await self._on_failure(e)
                logger.error("rewrite_call_failed", error=str(e))
                return RewriteResult(
                    rewritten_query=query, confidence=0.0, degraded=True
                )

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()

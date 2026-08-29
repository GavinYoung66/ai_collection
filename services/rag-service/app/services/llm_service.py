"""LLM 回答服务：开关门控 + 降级编排 + 合规红线（V5，design D8）。

- 门控：`KB_LLM_ANSWER_ENABLED=true` 且请求 `answer=true` 且检索未兜底（context 非空）才调用；
- 合规红线：注入 LLM 前对 chunk 文本脱敏（手机号/身份证/金额 → 占位符）+ 前置合规拦截 +
  后置内容校验（命中红线丢弃回答按降级处理，零 LLM 调用）；
- 降级：`LLM_TIMEOUT` 超时/网络异常/Provider 不可用 → 捕获返回 answer=None（不抛错）；
  连续 3 次失败置熔断标识 `rag:llm:degraded`（TTL 60s，复用既有熔断风格），窗口内短路；
- 并发上限：`LLM_MAX_CONCURRENCY`（默认 4）信号量，超限等待（不降级）。
"""
import asyncio
import re
import time

import structlog
from redis.asyncio import Redis
from redis.exceptions import RedisError

from common.config.settings import settings
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

_DEGRADED_KEY = "rag:llm:degraded"
_FAILURE_THRESHOLD = 3
_CIRCUIT_TTL = 60
_WORDS_TTL = 300  # 敏感词集缓存 TTL（秒）

# 脱敏规则（注入 prompt 前替换为占位符，对齐 03_db_vector_spec【强制8】）
_PHONE_RE = re.compile(r"1[3-9]\d{9}")
_IDCARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_AMOUNT_RE = re.compile(r"(?<!\d)\d+(?:,\d{3})*(?:\.\d+)?(?!\d)\s*(?:元|万|千|百|块|人民币|rmb|RMB)")


def mask_sensitive(text: str) -> str:
    """敏感字段脱敏：手机号/身份证/金额 → 占位符（供参数注入 prompt）。

    顺序：身份证（18 位，最长）→ 手机号（11 位）→ 金额。
    必须先替换身份证，否则手机号正则会把身份证中段（如 1990010112）截断，
    导致身份证规则失配而泄露。
    """
    t = _IDCARD_RE.sub("[身份证号]", text)
    t = _PHONE_RE.sub("[手机号]", t)
    t = _AMOUNT_RE.sub("[金额]", t)
    return t


class LLMService:
    def __init__(
        self,
        redis: Redis | None = None,
        provider=None,
        word_provider=None,
        max_concurrency: int | None = None,
    ):
        """word_provider: Callable[[], Awaitable[set[str]]]，返回敏感词集；None 时跳过红线校验。"""
        self._redis = redis
        self._provider = provider
        self._word_provider = word_provider
        self._provider_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(
            max_concurrency if max_concurrency is not None else settings.LLM_MAX_CONCURRENCY
        )
        self._failure_count = 0
        self._words_cache: tuple[set[str], float] | None = None

    async def _get_provider(self):
        """并发加锁懒加载 Provider（对齐 ASR 懒加载模式）。"""
        if self._provider is None:
            async with self._provider_lock:
                if self._provider is None:
                    from app.services.llm_providers.factory import LLMFactory

                    self._provider = LLMFactory.create(redis=self._redis)
        return self._provider

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
                try:
                    await self._redis.setex(_DEGRADED_KEY, _CIRCUIT_TTL, "1")
                except (RedisError, OSError) as e:
                    logger.warning("llm_degraded_set_failed", error=str(e))
            logger.error(
                "llm_circuit_break_open",
                failures=self._failure_count,
                error=str(error),
                trace_id=get_trace_id(),
            )

    async def _get_words(self) -> set[str]:
        if self._word_provider is None:
            return set()
        now = time.monotonic()
        if self._words_cache is None or now - self._words_cache[1] > _WORDS_TTL:
            words = await self._word_provider()
            self._words_cache = (set(words), now)
        return self._words_cache[0]

    async def _contains_redline(self, text: str) -> bool:
        words = await self._get_words()
        if not words:
            return False
        return any(w in text for w in words)

    async def generate_answer(
        self, query: str, context: list[dict], answer_requested: bool
    ) -> dict:
        """生成回答（若门控满足）。

        Args:
            query: 用户提问。
            context: 召回片段列表，项含 content/doc_id/page（已按分排序）。
            answer_requested: 请求是否显式开启 answer=true。

        Returns:
            {"answer": str | None, "citations": list[str], "degraded": bool, "llm_called": bool}
            - 开关关闭/未请求/context 空 → (None, [], False, False)，零 LLM 调用（V4 行为）；
            - 熔断短路/前置拦截 → (None, [], True, False)，零 LLM 调用（llm_called='0'）；
            - 超时/失败/后置拦截 → (None, [], True, True)（已发起调用后降级）。
        """
        if not (settings.KB_LLM_ANSWER_ENABLED and answer_requested and context):
            reason = "switch_off"
            if not settings.KB_LLM_ANSWER_ENABLED:
                reason = "KB_LLM_ANSWER_ENABLED=false"
            elif not answer_requested:
                reason = "answer_not_requested"
            elif not context:
                reason = "empty_context"
            return {
                "answer": None,
                "citations": [],
                "degraded": False,
                "llm_called": False,
                "llm_skip_reason": reason,
            }

        # 熔断短路：本次未发起任何 LLM 调用（llm_called=False），按降级处理
        if await self._is_degraded():
            logger.info("llm_circuit_break_short_circuit", trace_id=get_trace_id())
            return {
                "answer": None,
                "citations": [],
                "degraded": True,
                "llm_called": False,
                "llm_skip_reason": "circuit_break_open",
            }

        # P2：前置合规拦截仅针对「案件/录音转写」类来源；制度/话术/合规手册（含 other）
        # 常引用违禁词示例，不应误拦 LLM。
        _REDLINE_BLOCK_CATEGORIES = frozenset({"case"})
        _REDLINE_BLOCK_SOURCES = frozenset({"asr"})
        for c in context:
            cat = (c.get("category") or "").lower()
            src = (c.get("source") or "").lower()
            if cat not in _REDLINE_BLOCK_CATEGORIES and src not in _REDLINE_BLOCK_SOURCES:
                continue
            if await self._contains_redline(c["content"]):
                logger.warning(
                    "llm_pre_redline_blocked",
                    doc_id=c.get("doc_id"),
                    category=cat or None,
                    source=src or None,
                    trace_id=get_trace_id(),
                )
                return {
                    "answer": None,
                    "citations": [],
                    "degraded": True,
                    "llm_called": False,
                    "llm_skip_reason": "pre_redline_blocked",
                }

        # 脱敏后参数注入 prompt
        masked = [{**c, "content": mask_sensitive(c["content"])} for c in context]

        async with self._sem:
            provider = await self._get_provider()
            try:
                answer = await asyncio.wait_for(
                    provider.generate(masked, query, timeout=settings.LLM_TIMEOUT),
                    timeout=settings.LLM_TIMEOUT,
                )
            except Exception as e:
                await self._on_failure(e)
                logger.warning(
                    "llm_answer_degraded",
                    error=str(e),
                    trace_id=get_trace_id(),
                )
                return {
                    "answer": None,
                    "citations": [],
                    "degraded": True,
                    "llm_called": True,
                    "llm_skip_reason": f"provider_error:{type(e).__name__}",
                }
        await self._on_success()

        # 后置内容校验：回答命中红线 → 丢弃回答按降级处理
        if await self._contains_redline(answer.text):
            logger.warning("llm_post_redline_blocked", trace_id=get_trace_id())
            return {
                "answer": None,
                "citations": [],
                "degraded": True,
                "llm_called": True,
                "llm_skip_reason": "post_redline_blocked",
            }

        return {
            "answer": answer.text,
            "citations": list(answer.citations),
            "degraded": False,
            "llm_called": True,
            "llm_skip_reason": None,
        }

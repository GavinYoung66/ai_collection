"""LLM 回答服务：开关门控 + invoke_llm 编排 + 合规红线。

- 门控：`KB_LLM_ANSWER_ENABLED=true` 且请求 `answer=true` 且检索未兜底（context 非空）才调用；
- 合规红线：注入 LLM 前对 chunk 文本脱敏 + 前置合规拦截 + 后置内容校验；
- 调用：脱敏/前置拦截之后走 `invoke_llm`（限流→熔断→重试→fallback），transport 由 factory 注入现网通道；
- 整链失败 / 熔断 open / 模型维限流 → answer=None + degraded=true，不抛错；
- 并发上限：`LLM_MAX_CONCURRENCY` 信号量包住 invoke_llm（超限等待，不降级）。
"""
import asyncio
import re
import time

import structlog
from redis.asyncio import Redis

from app.settings import settings
from app.services.llm_providers.base import build_answer_prompt
from common.guardrails import postcheck_text, precheck_text
from common.llm.reliability import (
    LLMCircuitOpenError,
    LLMRateLimitError,
    invoke_llm,
)
from common.resilience.timeout import Timeout
from common.trace.middleware import get_trace_id

logger = structlog.get_logger()

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
        *,
        transport=None,
        model: str | None = None,
        fallback_chain: list[str] | None = None,
    ):
        """word_provider: Callable[[], Awaitable[set[str]]]，返回敏感词集；None 时跳过红线校验。

        transport/model/fallback_chain 供测试注入；生产路径由 factory 按 LLM_PROVIDER 解析。
        redis 保留与 deps 兼容（熔断改由 invoke_llm 写 rag:llm_degraded:{model}）。
        """
        self._redis = redis
        self._word_provider = word_provider
        self._transport = transport or provider
        self._model = model
        self._fallback_chain = fallback_chain
        self._resolved: tuple[str, list[str], object] | None = None
        self._provider_lock = asyncio.Lock()
        self._sem = asyncio.Semaphore(
            max_concurrency if max_concurrency is not None else settings.LLM_MAX_CONCURRENCY
        )
        self._words_cache: tuple[set[str], float] | None = None

    async def _resolve_llm(self) -> tuple[str, list[str], object]:
        if self._transport is not None:
            model = (self._model or settings.LLM_MODEL or "qwen-plus").strip() or "qwen-plus"
            chain = list(self._fallback_chain) if self._fallback_chain is not None else []
            return model, chain, self._transport
        async with self._provider_lock:
            if self._resolved is None:
                from app.services.llm_providers.factory import LLMFactory

                self._resolved = LLMFactory.create()
            return self._resolved

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
        return not postcheck_text(text, words=sorted(words)).ok

    def _citations(self, context: list[dict]) -> list[str]:
        return sorted({c["doc_id"] for c in context if c.get("doc_id")})

    def _degraded(
        self, *, llm_called: bool, reason: str, citations: list[str] | None = None
    ) -> dict:
        logger.warning(
            "llm_answer_degraded",
            reason=reason,
            trace_id=get_trace_id(),
        )
        return {
            "answer": None,
            "citations": citations or [],
            "degraded": True,
            "llm_called": llm_called,
            "llm_skip_reason": reason,
        }

    async def generate_answer(
        self, query: str, context: list[dict], answer_requested: bool
    ) -> dict:
        """生成回答（若门控满足）。

        Returns:
            {"answer": str | None, "citations": list[str], "degraded": bool, "llm_called": bool}
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

        # P2：前置合规拦截仅针对「案件/录音转写」类来源；制度/话术/合规手册（含 other）
        # 常引用违禁词示例，不应误拦 LLM。
        _REDLINE_BLOCK_CATEGORIES = frozenset({"case"})
        _REDLINE_BLOCK_SOURCES = frozenset({"asr"})
        redline_words = sorted(await self._get_words())
        for c in context:
            cat = (c.get("category") or "").lower()
            src = (c.get("source") or "").lower()
            if cat not in _REDLINE_BLOCK_CATEGORIES and src not in _REDLINE_BLOCK_SOURCES:
                continue
            if redline_words and not precheck_text(c["content"], words=redline_words).ok:
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

        masked = [{**c, "content": mask_sensitive(c["content"])} for c in context]
        system, user = build_answer_prompt(masked, query)
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        citations = self._citations(masked)
        model, fallback_chain, transport = await self._resolve_llm()

        async with self._sem:
            try:
                result = await invoke_llm(
                    model,
                    messages,
                    fallback_chain=fallback_chain,
                    timeout=Timeout(total=float(settings.LLM_TIMEOUT)),
                    transport=transport,
                )
            except LLMRateLimitError:
                return self._degraded(llm_called=True, reason="llm_rate_limited")
            except LLMCircuitOpenError:
                return self._degraded(llm_called=True, reason="llm_circuit_open")
            except Exception as e:
                return self._degraded(
                    llm_called=True, reason=f"provider_error:{type(e).__name__}"
                )

        content = result.get("content") if isinstance(result, dict) else None
        if not content:
            return self._degraded(
                llm_called=True,
                reason=result.get("error") or "chain_failed",
            )

        if await self._contains_redline(content):
            logger.warning("llm_post_redline_blocked", trace_id=get_trace_id())
            return {
                "answer": None,
                "citations": [],
                "degraded": True,
                "llm_called": True,
                "llm_skip_reason": "post_redline_blocked",
            }

        return {
            "answer": content,
            "citations": citations,
            "degraded": False,
            "llm_called": True,
            "llm_skip_reason": None,
        }

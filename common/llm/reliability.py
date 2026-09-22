"""LLM 调用可靠性：重试 + Redis 降级约定（在 transport 之上组合）。

- Transport：只发 HTTP
- 本模块：bounded retry、服务前缀 degrade key、``invoke_llm`` 统一编排
- 多实例以 Redis degrade 为准；熔断用 common.resilience.breaker 的 per-key 实例
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from redis.exceptions import RedisError

from common.config.settings import settings
from common.resilience._redis import get_redis
from common.resilience.breaker import BreakerRegistry, make_llm_breaker_key
from common.resilience.rate_limit import allow as rate_limit_allow
from common.resilience.timeout import Timeout
from common.llm.transport import message_content_from_completions, post_chat_completions

T = TypeVar("T")

PLACEHOLDER_SECRETS = frozenset(
    {"", "change_me", "<change_me>", "dev_cot_key_stub_change_me"}
)


def is_placeholder_secret(value: str | None) -> bool:
    """已知占位符密钥视为未配置。"""
    return (value or "").strip() in PLACEHOLDER_SECRETS


def require_real_secret(value: str | None, *, label: str = "API key") -> str:
    """真路径：占位或空则 fail-closed。"""
    text = (value or "").strip()
    if is_placeholder_secret(text):
        raise RuntimeError(f"{label} is missing or placeholder (change_me)")
    return text


class RedisDegradeTracker:
    """服务前缀 Redis 降级：连续失败达阈值后 setex；成功清零本地计数。"""

    def __init__(
        self,
        redis,
        *,
        key: str,
        failure_threshold: int = 3,
        ttl_seconds: int = 60,
    ):
        self._redis = redis
        self.key = key
        self.failure_threshold = failure_threshold
        self.ttl_seconds = ttl_seconds
        self._failure_count = 0

    async def is_degraded(self) -> bool:
        if self._redis is None:
            return False
        try:
            return await self._redis.exists(self.key) > 0
        except (RedisError, OSError, AttributeError):
            return False

    async def on_success(self) -> None:
        self._failure_count = 0
        if self._redis is None:
            return
        try:
            await self._redis.delete(self.key)
        except (RedisError, OSError, AttributeError):
            return

    async def on_failure(self, error: BaseException | None = None) -> None:
        self._failure_count += 1
        if self._failure_count < self.failure_threshold:
            return
        if self._redis is None:
            return
        try:
            await self._redis.setex(self.key, self.ttl_seconds, "1")
        except (RedisError, OSError, AttributeError):
            return


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    max_attempts: int = 3,
    delay: float = 1.0,
    backoff: float = 2.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> T:
    """对无参 async 工厂做指数退避重试。"""
    last: BaseException | None = None
    current = delay
    for attempt in range(max_attempts):
        try:
            return await fn()
        except exceptions as e:
            last = e
            if attempt >= max_attempts - 1:
                break
            await asyncio.sleep(current)
            current *= backoff
    assert last is not None
    raise last


# ---------------------------------------------------------------------------
# 服务名解析（per-key 熔断/限流 key 派生依赖；禁止无前缀全局 key）
# ---------------------------------------------------------------------------
_default_service: str | None = None


def set_default_service(name: str | None) -> None:
    """设置宿主服务名（csmp/rag/qc），服务启动时调用一次。"""
    global _default_service
    _default_service = (name or "").strip() or None


def resolve_service_name() -> str:
    """解析宿主服务名：显式设置优先，其次 settings.SERVICE_NAME；均无则 fail-fast。

    绝不静默回退到无前缀 key——那会复现"一个 key 干掉全平台"。
    """
    if _default_service:
        return _default_service
    name = (getattr(settings, "SERVICE_NAME", "") or "").strip()
    if not name:
        raise RuntimeError(
            "服务名未配置：请先 set_default_service('csmp'|'rag'|'qc') "
            "或设置 settings.SERVICE_NAME"
        )
    return name


# ---------------------------------------------------------------------------
# invoke_llm 统一编排：选Key → 限流 → 熔断 → 有界重试 → fallback → 降级
# ---------------------------------------------------------------------------
_LLM_RATE_LIMIT = 60       # 每窗口每模型请求数（P0 统一默认）
_LLM_RATE_WINDOW = 60      # 限流窗口（秒）
_LLM_MAX_ATTEMPTS = 3      # 业务错有界重试次数
_LLM_RETRY_DELAY = 1.0     # 重试基础退避（秒），指数递增
_DEGRADE_TTL_SECONDS = 60  # primary 降级标志 TTL
_DASHSCOPE_COMPAT_BASE = "https://dashscope.aliyuncs.com/compatible-mode/v1"

_llm_breakers = BreakerRegistry(
    fail_max=settings.LLM_CIRCUIT_BREAKER_THRESHOLD,
    reset_timeout=settings.LLM_CIRCUIT_BREAKER_RECOVERY,
)

LLMTransport = Callable[[str, list[dict], float], Awaitable[str]]

_llm_transport: LLMTransport | None = None


def set_llm_transport(fn: LLMTransport | None) -> None:
    """注入 LLM 调用实现 (model, messages, timeout_seconds) -> str；测试/多通道用。"""
    global _llm_transport
    _llm_transport = fn


def reset_llm_transport() -> None:
    global _llm_transport
    _llm_transport = None


def _resolve_llm_api_key() -> str:
    """默认通道的 Key 解析：优先 LLM_API_KEY，其次 DASHSCOPE_API_KEY。"""
    key = (settings.LLM_API_KEY or "").strip()
    if key and not is_placeholder_secret(key):
        return key
    return (settings.DASHSCOPE_API_KEY or "").strip()


async def _default_llm_transport(
    model: str, messages: list[dict], timeout_seconds: float
) -> str:
    """默认 DashScope 兼容通道（与 provider.chat 同源）。"""
    api_key = require_real_secret(
        _resolve_llm_api_key(), label="LLM_API_KEY / DASHSCOPE_API_KEY"
    )
    body = await post_chat_completions(
        base_url=_DASHSCOPE_COMPAT_BASE,
        api_key=api_key,
        model=model,
        messages=messages,
        timeout=timeout_seconds,
    )
    content = message_content_from_completions(body)
    if not content:
        raise RuntimeError("LLM returned empty content")
    return content


class LLMCircuitOpenError(RuntimeError):
    """LLM per-key 熔断打开，本次调用被快速拒绝。"""


class LLMRateLimitError(RuntimeError):
    """LLM 模型维度限流拒绝（``{svc}:llm_ratelimit:{model}`` 超窗口阈值）。

    继承 RuntimeError：保持旧 ``except RuntimeError`` 捕获语义向后兼容；
    服务层应据此映射为明确的 error_type（如 ``llm_rate_limited``），
    而非把裸 "RuntimeError" 落库。属于内部自保护限流，非用户维 B0006。
    """


async def mark_degraded_flag(key: str, *, ttl_seconds: int = _DEGRADE_TTL_SECONDS) -> None:
    """置位服务前缀 Redis 降级标志（一次失败即标记，TTL 到期自动恢复）。

    Redis 挂时静默（RedisDegradeTracker 内部容错），不阻断编排主流程。
    当前 threshold=1：每次调用新建 tracker（无状态残留，语义即"失败即标记"）；
    若后续需要"连续 N 次失败才置位"，应改为注册表持有跨调用 tracker 实例。
    """
    tracker = RedisDegradeTracker(
        get_redis(), key=key, failure_threshold=1, ttl_seconds=ttl_seconds
    )
    await tracker.on_failure()


async def _call_model(
    transport: LLMTransport,
    model: str,
    messages: list[dict],
    timeout: Timeout,
    breaker,
) -> str:
    """单模型完整调用：熔断检查 → 有界重试（仅业务错，超时不重试）。

    熔断计数口径：重试中间失败不计数，模型最终失败（重试耗尽或超时）计 1 次，
    避免内部重试放大熔断。
    """
    if not await breaker.before_call():
        raise LLMCircuitOpenError(f"LLM circuit open: {breaker.key}")
    for attempt in range(_LLM_MAX_ATTEMPTS):
        try:
            content = await asyncio.wait_for(
                transport(model, messages, timeout.total), timeout.total
            )
        except asyncio.TimeoutError:
            # 超时不重试：直接判失败，交给上层换 fallback
            await breaker.record_failure()
            raise
        except Exception as e:
            if attempt >= _LLM_MAX_ATTEMPTS - 1:
                await breaker.record_failure(e)
                raise
            await asyncio.sleep(_LLM_RETRY_DELAY * (2**attempt))
        else:
            await breaker.record_success()
            return content
    raise RuntimeError("unreachable")  # pragma: no cover


async def invoke_llm(
    model: str,
    messages: list[dict],
    *,
    fallback_chain: list[str] | None = None,
    timeout: Timeout = Timeout(),
    breaker_key: str | None = None,
    transport: LLMTransport | None = None,
) -> dict:
    """统一 LLM 调用编排：选Key → 限流 → 熔断检查 → 有界重试 → fallback 换模型 → 降级。

    - breaker_key 不传则自动派生 ``{svc}:llm_degraded:{model}``（per-service-per-model）；
    - 超时（超过 ``timeout.total``）不重试，直接换下一模型；
    - transport 可逐调用注入 ``(model, messages, timeout_seconds) -> str``（服务自有
      transport 适配，向后兼容；缺省用 set_llm_transport 注入值或 DashScope 默认通道）；
    - primary 失败即置位其降级标志；全链失败返回 degraded outcome（不构造伪造结果）。
    """
    transport = transport or _llm_transport or _default_llm_transport
    svc = resolve_service_name()
    primary_breaker_key = breaker_key or make_llm_breaker_key(svc, model)
    chain = [model] + [m for m in (fallback_chain or []) if m and m != model]

    # 限流（primary 模型维度；Redis 挂时由零件静默放行）。
    # 拒绝时抛类型化 LLMRateLimitError（RuntimeError 子类，向后兼容）；
    # 服务层据此映射明确 error_type（如 llm_rate_limited）→ 节点/人审兜底，
    # 不应把裸 "RuntimeError" 落 exception_info。
    rate_key = f"{svc}:llm_ratelimit:{model}"
    if not await rate_limit_allow(rate_key, limit=_LLM_RATE_LIMIT, window=_LLM_RATE_WINDOW):
        raise LLMRateLimitError(f"LLM rate limit exceeded: {rate_key}")

    errors: list[str] = []
    for index, candidate in enumerate(chain):
        key = primary_breaker_key if index == 0 else make_llm_breaker_key(svc, candidate)
        breaker = _llm_breakers.get_or_create(key)
        try:
            content = await _call_model(transport, candidate, messages, timeout, breaker)
        except Exception as e:
            errors.append(f"{candidate}: {e}")
            if index == 0:
                await mark_degraded_flag(primary_breaker_key)
            continue
        return {
            "model": candidate,
            "content": content,
            "fallback_used": index > 0,
            "degraded": index > 0,
            "error": None,
        }

    return {
        "model": model,
        "content": None,
        "fallback_used": len(chain) > 1,
        "degraded": True,
        "error": " | ".join(errors) or "all models failed",
    }

"""invoke_llm 统一编排（单一职责：选Key → 限流 → 熔断 → 有界重试 → fallback → 降级）。

只组合本包各零件（retry/degrade/rate_limit/instrumentation/service）与
common.resilience 熔断原语，不自行实现任何可靠性语义。
"""
from __future__ import annotations

import asyncio
import contextvars
import time
from collections.abc import Awaitable, Callable

from common.config.settings import settings
from common.observability.llm_metrics import LLM_METRICS
from common.resilience.breaker import BreakerRegistry, make_llm_breaker_key
from common.resilience.rate_limit import allow as rate_limit_allow
from common.resilience.timeout import Timeout
from common.llm.transport import (
    _llm_last_usage,
    message_content_from_completions,
    post_chat_completions,
)
from common.llm.reliability.degrade import mark_degraded_flag
from common.llm.reliability.instrumentation import (
    _llm_fallback_from,
    _record_llm_call,
)
from common.llm.reliability.rate_limit import get_llm_rate_limit
from common.llm.reliability.secrets import (
    is_placeholder_secret,
    require_real_secret,
)
from common.llm.reliability.service import resolve_service_name

from common.resilience.retry import LLM_RETRY, retry_delay

_LLM_MAX_ATTEMPTS = LLM_RETRY.max_attempts  # 业务错有界重试次数
_LLM_RETRY_DELAY = LLM_RETRY.base_delay     # 重试基础退避（秒），指数递增
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


# ---------------------------------------------------------------------------
# 编排上下文标志：防止服务自有 transport 与 invoke_llm 对同一请求重复计数
# ---------------------------------------------------------------------------
# 投诉 RealLLMTransport 内嵌 rate_limit.allow 补齐段（防御不经编排的直调场景）；
# 而生产真路径是 ComplaintLLMProvider → invoke_llm(transport=RealLLMTransport)
# 嵌套调用，若两层各 INCR 一次，同一 {svc}:llm_ratelimit:{model} 会被计两次
# （默认配额名义 60、实际仅 30 次/窗口）。invoke_llm 限流通过后置位本标志，
# transport 检测到即跳过本地计数；直调 transport（标志为 False）时计数照常保留。
# 用 ContextVar 而非全局布尔：asyncio 任务间天然隔离，嵌套链同任务可见，
# 且 invoke_llm 退出时 reset，不污染调用方上下文。
_llm_orchestration_active: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "llm_orchestration_active", default=False
)


def is_llm_orchestration_active() -> bool:
    """当前调用是否处于 invoke_llm 编排内（供服务自有 transport 避免重复限流计数）。"""
    return _llm_orchestration_active.get()


class LLMCircuitOpenError(RuntimeError):
    """LLM per-key 熔断打开，本次调用被快速拒绝。"""


class LLMRateLimitError(RuntimeError):
    """LLM 模型维度限流拒绝（``{svc}:llm_ratelimit:{model}`` 超窗口阈值）。

    继承 RuntimeError：保持旧 ``except RuntimeError`` 捕获语义向后兼容；
    服务层应据此映射为明确的 error_type（如 ``llm_rate_limited``），
    而非把裸 "RuntimeError" 落库。属于内部自保护限流，非用户维 B0006。
    """


async def _invoke_transport_once(
    transport: LLMTransport, model: str, messages: list[dict], timeout_seconds: float
) -> tuple[str, dict | None]:
    """单次真实 transport 调用，返回 ``(content, 本次 usage)``。

    usage 必须在**与 transport 调用同一个 asyncio Task 上下文**内读取：
    ``_call_model`` 用 ``asyncio.wait_for`` 包裹本协程，wait_for 会把协程包成新 Task
    并复制 ContextVar 上下文——若在外层 ``_call_model`` 主体读取，
    ``post_chat_completions`` 在子 Task 内写入的 usage 将不可见（实测恒为 None）。
    读完立即清零，避免同一子 Task 内后续读取污染。
    入口先清零：防御“写 usage 后失败”的残留——如 post_chat_completions
    成功写入 usage 后 transport 侧校验抛错，读后清零不会执行；
    若不预清，重试/下一候选成功但未写 usage 时会串到旧 token。
    """
    _llm_last_usage.set(None)
    content = await transport(model, messages, timeout_seconds)
    usage = _llm_last_usage.get()
    _llm_last_usage.set(None)
    return content, usage


async def _call_model(
    transport: LLMTransport,
    model: str,
    messages: list[dict],
    timeout: Timeout,
    breaker,
) -> str:
    """单模型完整调用：熔断检查 → 有界重试（仅业务错，超时不重试）。

    熔断计数口径：重试中间失败不计数，模型最终失败（重试耗尽或超时）计 1 次，
    避免内部重试放大熔断。每次候选终态同步打 llm_call 日志与指标（spec 三埋点之①②）。
    """
    svc = resolve_service_name()
    fallback_from = _llm_fallback_from.get()
    if not await breaker.before_call():
        # 埋点②：熔断打开拒绝（transport 未被调用，token/latency 全 0）
        _record_llm_call(
            svc=svc,
            model=model,
            fallback_from=fallback_from,
            breaker_key=breaker.key,
            status="breaker_open",
            latency_seconds=0.0,
        )
        LLM_METRICS.breaker_open_total.labels(svc, model).inc()
        raise LLMCircuitOpenError(f"LLM circuit open: {breaker.key}")
    for attempt in range(_LLM_MAX_ATTEMPTS):
        started = time.perf_counter()
        try:
            content, usage = await asyncio.wait_for(
                _invoke_transport_once(transport, model, messages, timeout.total),
                timeout.total,
            )
        except asyncio.TimeoutError:
            # 超时不重试：直接判失败，交给上层换 fallback（埋点① status=timeout）
            _record_llm_call(
                svc=svc,
                model=model,
                fallback_from=fallback_from,
                breaker_key=breaker.key,
                status="timeout",
                latency_seconds=time.perf_counter() - started,
            )
            await breaker.record_failure()
            raise
        except Exception as e:
            if attempt >= _LLM_MAX_ATTEMPTS - 1:
                # 埋点①：重试耗尽终态 error（中间失败不埋点，避免放大）
                _record_llm_call(
                    svc=svc,
                    model=model,
                    fallback_from=fallback_from,
                    breaker_key=breaker.key,
                    status="error",
                    latency_seconds=time.perf_counter() - started,
                )
                await breaker.record_failure(e)
                raise
            await asyncio.sleep(
                retry_delay(
                    attempt + 1, LLM_RETRY.with_overrides(base_delay=_LLM_RETRY_DELAY)
                )
            )
        else:
            # 埋点①：成功终态 ok（usage 由内层协程在同 Task 上下文读取透传）
            _record_llm_call(
                svc=svc,
                model=model,
                fallback_from=fallback_from,
                breaker_key=breaker.key,
                status="ok",
                latency_seconds=time.perf_counter() - started,
                usage=usage,
            )
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
    # 入口第一行 reset：防上一次调用残留污染本次（成功/异常路径均由 finally 兜底清零）
    _llm_last_usage.set(None)
    transport = transport or _llm_transport or _default_llm_transport
    svc = resolve_service_name()
    primary_breaker_key = breaker_key or make_llm_breaker_key(svc, model)
    chain = [model] + [m for m in (fallback_chain or []) if m and m != model]

    # 限流（primary 模型维度；Redis 挂时由零件静默放行）。
    # 拒绝时抛类型化 LLMRateLimitError（RuntimeError 子类，向后兼容）；
    # 服务层据此映射明确 error_type（如 llm_rate_limited）→ 节点/人审兜底，
    # 不应把裸 "RuntimeError" 落 exception_info。
    rate_key = f"{svc}:llm_ratelimit:{model}"
    rate_limit_value, rate_window_value = get_llm_rate_limit()
    if not await rate_limit_allow(
        rate_key, limit=rate_limit_value, window=rate_window_value
    ):
        # 埋点③：限流拒绝（未进入 _call_model，token/latency 全 0；不计入 llm_calls_total）
        _record_llm_call(
            svc=svc,
            model=model,
            fallback_from=None,
            breaker_key=primary_breaker_key,
            status="rate_limited",
            latency_seconds=0.0,
        )
        LLM_METRICS.rate_limited_total.labels(svc, model).inc()
        raise LLMRateLimitError(f"LLM rate limit exceeded: {rate_key}")

    # 置位编排标志：本函数内对 transport 的调用（含 fallback 换模型）都处于编排中，
    # 服务自有 transport 据此跳过其内嵌的同 key 计数，杜绝同一请求双 INCR（D-1）。
    orchestration_token = _llm_orchestration_active.set(True)
    try:
        errors: list[str] = []
        for index, candidate in enumerate(chain):
            key = primary_breaker_key if index == 0 else make_llm_breaker_key(svc, candidate)
            breaker = _llm_breakers.get_or_create(key)
            # fallback_from：primary 候选为 None，第 i>0 候选为 chain[i-1]
            _llm_fallback_from.set(None if index == 0 else chain[index - 1])
            try:
                content = await _call_model(transport, candidate, messages, timeout, breaker)
            except Exception as e:
                errors.append(f"{candidate}: {e}")
                if index == 0:
                    await mark_degraded_flag(primary_breaker_key)
                if index + 1 < len(chain):
                    # fallback 链切换计数（仅在确有下一候选时记；continue 之前埋点）
                    LLM_METRICS.fallback_total.labels(
                        svc, candidate, chain[index + 1]
                    ).inc()
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
    finally:
        # 残留兜底：读取并清零（成功路径已在 _call_model 内层协程读取；此处防异常路径残留）
        _ = _llm_last_usage.get()
        _llm_last_usage.set(None)
        _llm_fallback_from.set(None)
        _llm_orchestration_active.reset(orchestration_token)

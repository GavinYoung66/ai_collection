"""LLM per-call 埋点（单一职责：structlog event=llm_call + Prometheus 指标）。

埋点不得影响主调用链：上游返回脏值时按 0 记，绝不让埋点异常把成功的
transport 调用改判为失败。
"""
from __future__ import annotations

import contextvars

import structlog

from common.observability.llm_metrics import LLM_METRICS

logger = structlog.get_logger()

# 当前 _call_model 候选在 fallback 链中的来源模型（primary 候选为 None）。
# 用 ContextVar 传递替代给 _call_model 加参：保持其对外签名不变；
# invoke_llm 在每次候选调用前置位、退出时复位。
_llm_fallback_from: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_fallback_from", default=None
)


def _record_llm_call(
    *,
    svc: str,
    model: str,
    fallback_from: str | None,
    breaker_key: str,
    status: str,
    latency_seconds: float,
    usage: dict | None = None,
) -> None:
    """LLM 每次编排终态埋点：structlog 日志 + Prometheus 指标。

    口径（llm-usage-observability spec）：
    - ``llm_calls_total`` / ``llm_call_latency_seconds`` 仅计真实 transport 调用
      （``status ∈ {ok, timeout, error}``）；``breaker_open`` / ``rate_limited`` 不计入；
    - ``llm_tokens_total`` 仅 ``status=ok`` 且 ``token_source=usage`` 时按真实值计数，其余 +0；
    - ``latency_ms`` 由真实调用耗时换算；``breaker_open`` / ``rate_limited`` 传 0。
    """
    token_source = "usage" if isinstance(usage, dict) else "missing"
    prompt_tokens = 0
    completion_tokens = 0
    if isinstance(usage, dict):
        # 埋点不得影响主调用链：上游返回脏值（非数值 token 字段）时按 0 记，
        # 绝不让埋点异常把成功的 transport 调用改判为失败。
        try:
            prompt_tokens = int(usage.get("prompt_tokens") or 0)
            completion_tokens = int(usage.get("completion_tokens") or 0)
        except (TypeError, ValueError):
            prompt_tokens = 0
            completion_tokens = 0

    logger.info(
        "llm_call",
        svc=svc,
        model=model,
        fallback_from=fallback_from,
        breaker_key=breaker_key,
        status=status,
        latency_ms=round(latency_seconds * 1000, 3),
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
        token_source=token_source,
    )

    # 仅真实 transport 调用计入 calls/latency/tokens（拒绝态由独立计数器承载）
    if status not in ("ok", "timeout", "error"):
        return
    LLM_METRICS.calls_total.labels(svc, model, status).inc()
    LLM_METRICS.call_latency_seconds.labels(svc, model).observe(latency_seconds)
    if status == "ok" and token_source == "usage":
        LLM_METRICS.tokens_total.labels(svc, model, "prompt").inc(prompt_tokens)
        LLM_METRICS.tokens_total.labels(svc, model, "completion").inc(completion_tokens)

"""LLM 编排层 Prometheus 指标定义（仅定义+注册；打点埋点在 B3 批次 invoke_llm 落地）。

指标口径（spec: llm-usage-observability）：
- llm_calls_total / llm_call_latency_seconds 仅计真实 transport 调用（ok/timeout/error）；
  rate_limited 与 breaker_open 由独立指标承载，不计入 llm_calls_total；
- llm_tokens_total 按 usage 计数（kind=prompt|completion），token_source=missing 时 +0。
"""
from __future__ import annotations

from dataclasses import dataclass

from prometheus_client import REGISTRY, CollectorRegistry, Counter, Histogram

# llm_call_latency_seconds 直方图桶（spec 固定取值）
LLM_LATENCY_BUCKETS = (0.1, 0.5, 1, 2, 5, 10, 30, 60)


@dataclass(frozen=True)
class LLMMetrics:
    """LLM 六指标容器（svc/model 等标签区分服务与模型）。"""

    calls_total: Counter
    tokens_total: Counter
    call_latency_seconds: Histogram
    fallback_total: Counter
    rate_limited_total: Counter
    breaker_open_total: Counter


def build_llm_metrics(registry: CollectorRegistry) -> LLMMetrics:
    """在指定 registry 上构建并注册 6 个 LLM 指标（自定义 registry 供测试隔离用）。

    同一 registry 重复注册同名指标会抛 ValueError（prometheus_client 防重机制）。
    """
    return LLMMetrics(
        calls_total=Counter(
            "llm_calls_total",
            "LLM transport 真实调用终态计数（status=ok|timeout|error；限流/熔断不计入）",
            ["svc", "model", "status"],
            registry=registry,
        ),
        tokens_total=Counter(
            "llm_tokens_total",
            "LLM token 吞吐（kind=prompt|completion；token_source=missing 时 +0）",
            ["svc", "model", "kind"],
            registry=registry,
        ),
        call_latency_seconds=Histogram(
            "llm_call_latency_seconds",
            "LLM transport 调用延迟（仅真实调用）",
            ["svc", "model"],
            buckets=LLM_LATENCY_BUCKETS,
            registry=registry,
        ),
        fallback_total=Counter(
            "llm_fallback_total",
            "LLM fallback 链切换计数（from_model -> to_model）",
            ["svc", "from_model", "to_model"],
            registry=registry,
        ),
        rate_limited_total=Counter(
            "llm_rate_limited_total",
            "LLM 限流拒绝计数（未进入 transport 调用）",
            ["svc", "model"],
            registry=registry,
        ),
        breaker_open_total=Counter(
            "llm_breaker_open_total",
            "LLM 熔断打开拒绝计数（未进入 transport 调用）",
            ["svc", "model"],
            registry=registry,
        ),
    )


# 模块级默认实例：导入即注册到默认 REGISTRY，
# mount_metrics 暴露 /metrics 时一并输出（生产路径）。
LLM_METRICS = build_llm_metrics(REGISTRY)

"""common.observability.llm_metrics 单元测试：6 指标定义与注册（1.3）+ usage 透传（4.1）。

指标定义与注册属 B1 批次；post_chat_completions 的 usage ContextVar 透传属 B3 批次（4.1）。
"""
import math

import pytest
from fastapi import FastAPI
from prometheus_client import REGISTRY, CollectorRegistry, generate_latest

from common.llm import transport as transport_mod
from common.observability.llm_metrics import (
    LLM_LATENCY_BUCKETS,
    LLM_METRICS,
    build_llm_metrics,
)
from common.observability.metrics import mount_metrics


class TestLLMMetricDefinitions:
    """模块级默认实例：导入即注册到默认 REGISTRY（生产 mount_metrics 一并暴露）。"""

    def test_six_metrics_registered_on_default_registry(self):
        # 只读断言：prometheus_client 对零子序列的 collector 仍输出 HELP/TYPE 行，
        # 故无需写默认 REGISTRY（避免跨测试遗留零值时间序列污染全局状态）。
        # （若默认 REGISTRY 重复注册，模块导入期即抛 ValueError，收集阶段就会失败）
        exposition = generate_latest(REGISTRY).decode()
        for name in (
            "llm_calls_total",
            "llm_tokens_total",
            "llm_call_latency_seconds",
            "llm_fallback_total",
            "llm_rate_limited_total",
            "llm_breaker_open_total",
        ):
            assert f"# TYPE {name} " in exposition

    def test_label_names(self):
        assert LLM_METRICS.calls_total._labelnames == ("svc", "model", "status")
        assert LLM_METRICS.tokens_total._labelnames == ("svc", "model", "kind")
        assert LLM_METRICS.call_latency_seconds._labelnames == ("svc", "model")
        assert LLM_METRICS.fallback_total._labelnames == (
            "svc",
            "from_model",
            "to_model",
        )
        assert LLM_METRICS.rate_limited_total._labelnames == ("svc", "model")
        assert LLM_METRICS.breaker_open_total._labelnames == ("svc", "model")

    def test_latency_histogram_buckets(self):
        assert LLM_LATENCY_BUCKETS == (0.1, 0.5, 1, 2, 5, 10, 30, 60)
        # prometheus_client 内部把 buckets 归一化为 float 列表并追加 +inf
        assert LLM_METRICS.call_latency_seconds._upper_bounds == [
            *(float(b) for b in LLM_LATENCY_BUCKETS),
            math.inf,
        ]


class TestBuildLLMMetricsCustomRegistry:
    """自定义 CollectorRegistry 隔离构建（不污染全局 REGISTRY）。"""

    def test_build_and_observe(self):
        registry = CollectorRegistry()
        m = build_llm_metrics(registry)
        m.calls_total.labels("svc-a", "model-x", "ok").inc()
        m.tokens_total.labels("svc-a", "model-x", "prompt").inc(42)
        m.tokens_total.labels("svc-a", "model-x", "completion").inc(8)
        m.call_latency_seconds.labels("svc-a", "model-x").observe(1.5)
        m.fallback_total.labels("svc-a", "model-x", "model-y").inc()
        m.rate_limited_total.labels("svc-a", "model-x").inc()
        m.breaker_open_total.labels("svc-a", "model-x").inc()

        assert (
            registry.get_sample_value(
                "llm_calls_total",
                {"svc": "svc-a", "model": "model-x", "status": "ok"},
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "llm_tokens_total",
                {"svc": "svc-a", "model": "model-x", "kind": "prompt"},
            )
            == 42.0
        )
        assert (
            registry.get_sample_value(
                "llm_tokens_total",
                {"svc": "svc-a", "model": "model-x", "kind": "completion"},
            )
            == 8.0
        )
        assert (
            registry.get_sample_value(
                "llm_call_latency_seconds_count",
                {"svc": "svc-a", "model": "model-x"},
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "llm_fallback_total",
                {"svc": "svc-a", "from_model": "model-x", "to_model": "model-y"},
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "llm_rate_limited_total", {"svc": "svc-a", "model": "model-x"}
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "llm_breaker_open_total", {"svc": "svc-a", "model": "model-x"}
            )
            == 1.0
        )

    def test_duplicate_registration_raises(self):
        # 同一 registry 重复注册同名指标必须 ValueError（prometheus_client 防重机制）
        registry = CollectorRegistry()
        build_llm_metrics(registry)
        with pytest.raises(ValueError):
            build_llm_metrics(registry)

    def test_mount_metrics_registers_llm_metrics_on_custom_registry(self):
        # mount_metrics 在自定义 registry 上一并注册 LLM 指标 → 重复构建应抛 ValueError
        registry = CollectorRegistry()
        mount_metrics(FastAPI(), "svc-t", registry=registry)
        with pytest.raises(ValueError):
            build_llm_metrics(registry)


# ---------------------------------------------------------------------------
# 4.1 post_chat_completions 成功返回时把 usage 写入 _llm_last_usage
# ---------------------------------------------------------------------------
async def test_post_chat_completions_writes_usage_to_contextvar(monkeypatch):
    """成功拿到含 usage 的 dict body → ContextVar 有值。"""
    usage = {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9}

    async def _fake_post_json(**kwargs):
        return {"choices": [{"message": {"content": "hi"}}], "usage": usage}

    monkeypatch.setattr(transport_mod, "post_json", _fake_post_json)
    transport_mod._llm_last_usage.set(None)

    body = await transport_mod.post_chat_completions(
        base_url="https://llm.invalid", api_key="k", model="m", messages=[]
    )

    assert body["usage"] == usage
    assert transport_mod._llm_last_usage.get() == usage
    transport_mod._llm_last_usage.set(None)


async def test_post_chat_completions_without_usage_writes_none(monkeypatch):
    """body 不含 usage 字段 → 写入 None（覆盖上一次残留）。"""

    async def _fake_post_json(**kwargs):
        return {"choices": [{"message": {"content": "hi"}}]}

    monkeypatch.setattr(transport_mod, "post_json", _fake_post_json)
    transport_mod._llm_last_usage.set({"stale": 1})

    await transport_mod.post_chat_completions(
        base_url="https://llm.invalid", api_key="k", model="m", messages=[]
    )

    assert transport_mod._llm_last_usage.get() is None

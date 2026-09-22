"""6 节点 LLM 降级单测（spec: complaint-service/graph-nodes）。

覆盖：
1. 6 节点 LLM 抛 TimeoutError → [规则兜底] 草稿 + pending_review +
   exception_info + csmp.degrade.triggered(A5017) 事件，不抛异常；
2. 三类异常映射 error_type（TimeoutError→timeout /
   CircuitBreakerOpenError→circuit_open / LLMCallError→llm_degraded）；
3. 真链路变体：真实 ComplaintLLMProvider + 睡眠 transport +
   Timeout(total=0.1) → 整链降级 → 节点兜底。
"""
from __future__ import annotations

import asyncio

import pytest

from app.config import settings
from app.llm.provider import CircuitBreakerOpenError, ComplaintLLMProvider, LLMCallError
from app.models.state import (
    ComplianceOpinion,
    ComplaintSessionState,
    FactTimeline,
    UserDemandAnalysis,
    VerdictDraft,
    VerdictType,
)
from common.llm import reliability as llm_reliability
from common.resilience.timeout import Timeout

_RULE_PREFIX = "[规则兜底]"

# 节点名 → (模块路径, 节点函数名, 输出字段, 前缀断言字段)
_NODES: dict[str, tuple[str, str, str, str]] = {
    "verdict": ("app.graph.nodes.verdict", "verdict_node", "verdict_draft", "reasoning"),
    "fact": ("app.graph.nodes.fact", "fact_node", "fact_timeline", "summary"),
    "reflection": ("app.graph.nodes.reflection", "reflection_node", "reflection_report", "suggestion"),
    "compliance": ("app.graph.nodes.compliance", "compliance_node", "compliance_opinion", "details"),
    "user_demand": ("app.graph.nodes.user_demand", "user_demand_node", "user_demand", "demand_type"),
    "generate_package": (
        "app.graph.nodes.generate_package",
        "generate_package_node",
        "judgment_package",
        "response_draft",
    ),
}

_MCP_NODES = {"compliance", "generate_package"}


class _FailingProvider:
    """invoke 即抛指定异常的 stub provider（鸭子类型对齐 ComplaintLLMProvider）。"""

    def __init__(self, exc: BaseException):
        self._exc = exc
        self.calls = 0

    async def invoke(self, prompt, **kwargs):
        self.calls += 1
        raise self._exc


class _StubMCP:
    """MCP 客户端 stub：检索恒返回空列表。"""

    async def call(self, server, action, payload):
        return []


@pytest.fixture(autouse=True)
def _degrade_env(monkeypatch):
    """捕获韧性事件 + 隔离全局熔断注册表。"""
    import common.resilience.hooks as hooks_mod

    events: list[tuple[str, dict]] = []

    async def _capture(name, payload):
        events.append((name, payload))

    monkeypatch.setattr(hooks_mod, "on_event", _capture)
    llm_reliability._llm_breakers.clear()
    yield events
    llm_reliability._llm_breakers.clear()


def _base_state() -> ComplaintSessionState:
    return ComplaintSessionState(
        session_id="sess-1",
        trace_id="trace-1",
        complaint_text="要求退款，客服一直不处理",
    )


def _state_for(node: str) -> ComplaintSessionState:
    """按节点前置条件构造可到达 LLM 调用的 state。"""
    state = _base_state()
    if node == "verdict":
        # MODERATE-6：三输入缺失会短路转人工，需预置
        state.fact_timeline = FactTimeline(timeline=[], summary="时序")
        state.compliance_opinion = ComplianceOpinion(overall_compliance="uncertain")
        state.user_demand = UserDemandAnalysis(
            demand_type="refund", emotion_level="angry", secondary_complaint_risk="low"
        )
    elif node == "generate_package":
        # 防御性短路：verdict_draft 为空或 pending_review 都会 return {}
        state.verdict_draft = VerdictDraft(verdict=VerdictType.pending, confidence=0.5)
    return state


def _patch_llm(monkeypatch, node: str, provider: _FailingProvider) -> None:
    module, _, _, _ = _NODES[node]
    monkeypatch.setattr(f"{module}.get_llm_provider", lambda: provider)
    if node in _MCP_NODES:
        monkeypatch.setattr(f"{module}.get_mcp_client", lambda: _StubMCP())


async def _run_node(node: str, state: ComplaintSessionState) -> dict:
    module, func_name, _, _ = _NODES[node]
    import importlib

    func = getattr(importlib.import_module(module), func_name)
    return await func(state)


# ── 1. 六节点超时降级 ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("node", list(_NODES))
async def test_node_timeout_produces_rule_fallback(node, monkeypatch, _degrade_env):
    """LLM 超时 → [规则兜底] 草稿 + pending_review + exception_info + A5017 事件。"""
    provider = _FailingProvider(TimeoutError("mock timeout"))
    _patch_llm(monkeypatch, node, provider)
    state = _state_for(node)

    update = await _run_node(node, state)  # 不抛异常

    assert provider.calls == 1
    assert update["task_status"] == "pending_review"
    exc_info = update["exception_info"]
    assert exc_info["error_type"] == "timeout"
    assert exc_info["node"] == node
    out = update[_NODES[node][2]]
    assert out is not None
    assert _RULE_PREFIX in getattr(out, _NODES[node][3])  # 输出非空且带前缀

    assert len(_degrade_env) == 1
    name, payload = _degrade_env[0]
    assert name == "csmp.degrade.triggered"
    assert payload["code"] == "A5017"
    assert payload["reason"] == "timeout"
    assert payload["node"] == node
    assert payload["trace_id"] == "trace-1"


# ── 2. 异常类型映射 ─────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "exc,expected",
    [
        (TimeoutError("t"), "timeout"),
        (CircuitBreakerOpenError("c"), "circuit_open"),
        (LLMCallError("l"), "llm_degraded"),
    ],
)
async def test_exception_type_mapping(exc, expected, monkeypatch, _degrade_env):
    """三类可捕获异常 → error_type / 事件 reason 值域正确。"""
    node = "fact"
    _patch_llm(monkeypatch, node, _FailingProvider(exc))
    update = await _run_node(node, _state_for(node))

    assert update["exception_info"]["error_type"] == expected
    assert update["task_status"] == "pending_review"
    assert _degrade_env[0][1]["reason"] == expected


# ── 3. 真链路超时变体（timeout=0.1s）────────────────────


@pytest.mark.asyncio
async def test_real_provider_timeout_0_1s_degrades(monkeypatch, _degrade_env):
    """真实 provider + 睡眠 transport + Timeout(total=0.1)：
    整链耗尽 → LLMCallError → 节点兜底 [规则兜底] + pending_review。"""
    monkeypatch.setattr(settings, "LLM_FALLBACK_CHAIN", "mock-slow-model")
    monkeypatch.setattr(llm_reliability, "_LLM_RETRY_DELAY", 0.01)

    class _SleepyTransport:
        async def invoke(self, prompt, schema=None, *, model=None, **_):
            await asyncio.sleep(0.5)  # 远超 0.1s 预算
            return "{}"

    provider = ComplaintLLMProvider(transport=_SleepyTransport(), llm_timeout=Timeout(total=0.1))
    node = "user_demand"
    monkeypatch.setattr(f"{_NODES[node][0]}.get_llm_provider", lambda: provider)

    update = await _run_node(node, _state_for(node))

    assert update["task_status"] == "pending_review"
    assert _RULE_PREFIX in update["user_demand"].demand_type
    # 整链降级（degraded outcome）→ llm_degraded，而非裸 timeout
    assert update["exception_info"]["error_type"] == "llm_degraded"
    assert _degrade_env[0][1]["code"] == "A5017"

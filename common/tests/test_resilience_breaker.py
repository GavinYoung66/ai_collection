"""common.resilience.breaker 单测：fail_max 打开/half-open 单探针/成败转移/key 隔离。"""
import pytest

from common.resilience.breaker import (
    BreakerRegistry,
    BreakerState,
    CircuitBreaker,
    make_llm_breaker_key,
    make_mcp_breaker_key,
)
from common.resilience.hooks import set_publisher


class FakeClock:
    """可控时钟，half-open 测试无需真实 sleep。"""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _reset_publisher():
    set_publisher(None)
    yield
    set_publisher(None)


def test_valid_breaker_keys():
    CircuitBreaker("rag:llm_degraded:deepseek-chat")
    CircuitBreaker("csmp:mcp_degraded:rag_kb")
    assert make_llm_breaker_key("qc", "qwen-72b") == "qc:llm_degraded:qwen-72b"
    assert make_mcp_breaker_key("csmp", "rag_kb") == "csmp:mcp_degraded:rag_kb"


@pytest.mark.parametrize("bad_key", [
    "csmp:llm_degraded=1",      # 全局单 key，禁止
    "llm_degraded:deepseek",    # 缺服务前缀
    "csmp:other_degraded:x",    # 非 llm/mcp 降级域
    "csmp:llm_degraded:",       # 资源段为空
    "",
])
def test_invalid_breaker_key_rejected(bad_key):
    with pytest.raises(ValueError):
        CircuitBreaker(bad_key)


def test_invalid_params():
    with pytest.raises(ValueError):
        CircuitBreaker("csmp:llm_degraded:m1", fail_max=0)
    with pytest.raises(ValueError):
        CircuitBreaker("csmp:llm_degraded:m1", reset_timeout=0)


@pytest.mark.asyncio
async def test_fail_max_opens_breaker():
    clock = FakeClock()
    events = []

    async def publisher(name, payload):
        events.append((name, payload))

    set_publisher(publisher)
    cb = CircuitBreaker("csmp:llm_degraded:m1", fail_max=5, reset_timeout=30, clock=clock)

    for _ in range(4):
        await cb.record_failure(RuntimeError("boom"))
    assert cb.state == BreakerState.CLOSED.value
    assert await cb.before_call() is True

    await cb.record_failure(RuntimeError("boom"))  # 第 5 次连续失败
    assert cb.state == BreakerState.OPEN.value
    # open 期请求被拒
    assert await cb.before_call() is False
    # 打开瞬间发 hook 事件（reason 为固定语义字段，便于订阅方路由）
    assert events == [(
        "csmp.circuit_breaker.open",
        {
            "key": "csmp:llm_degraded:m1",
            "fail_max": 5,
            "reason": "circuit_open",
            "error": "boom",
        },
    )]


@pytest.mark.asyncio
async def test_success_resets_consecutive_failures():
    cb = CircuitBreaker("csmp:llm_degraded:m1", fail_max=3, clock=FakeClock())

    await cb.record_failure()
    await cb.record_failure()
    await cb.record_success()
    await cb.record_failure()
    await cb.record_failure()
    # 中间成功后连续失败计数清零，2 次不足 3 次，仍 closed
    assert cb.state == BreakerState.CLOSED.value
    assert await cb.before_call() is True


@pytest.mark.asyncio
async def test_half_open_allows_exactly_one_probe_then_success_closes():
    clock = FakeClock()
    cb = CircuitBreaker("rag:llm_degraded:deepseek-chat", fail_max=5,
                        reset_timeout=30, clock=clock)

    for _ in range(5):
        await cb.record_failure()
    assert cb.state == BreakerState.OPEN.value

    clock.advance(29)
    assert await cb.before_call() is False  # 未到 reset_timeout
    clock.advance(1)
    assert await cb.before_call() is True   # 唯一探针放行
    assert cb.state == BreakerState.HALF_OPEN.value
    # 并发探针按 open 拒绝
    assert await cb.before_call() is False

    await cb.record_success()
    assert cb.state == BreakerState.CLOSED.value
    assert await cb.before_call() is True
    # 闭合后失败计数已重置，不足 fail_max 不会立刻再开
    for _ in range(4):
        await cb.record_failure()
    assert cb.state == BreakerState.CLOSED.value


@pytest.mark.asyncio
async def test_half_open_probe_failure_reopens_and_resets_timer():
    clock = FakeClock(start=1000.0)
    events = []

    async def publisher(name, payload):
        events.append(name)

    set_publisher(publisher)
    cb = CircuitBreaker("csmp:mcp_degraded:rag_kb", fail_max=2,
                        reset_timeout=30, clock=clock)

    await cb.record_failure()
    await cb.record_failure()
    assert cb.state == BreakerState.OPEN.value  # t=1000 打开

    clock.advance(30)
    assert await cb.before_call() is True       # 探针放行
    await cb.record_failure()                   # 探针失败
    assert cb.state == BreakerState.OPEN.value  # 重新打开，计时从 t=1030 起

    clock.advance(29)
    assert await cb.before_call() is False
    clock.advance(1)
    assert await cb.before_call() is True       # 新周期探针

    # 首次打开 + 探针失败重开，共 2 次 open 事件
    assert events == ["csmp.circuit_breaker.open", "csmp.circuit_breaker.open"]


@pytest.mark.asyncio
async def test_service_prefixed_keys_are_isolated():
    # 投诉侧 MCP rag_kb 熔断，不影响 QC 的 LLM 与 rag 侧其他 MCP
    cb_csmp = CircuitBreaker("csmp:mcp_degraded:rag_kb", fail_max=1)
    cb_qc = CircuitBreaker("qc:llm_degraded:qwen-72b", fail_max=1)
    cb_rag = CircuitBreaker("rag:mcp_degraded:other_server", fail_max=1)

    await cb_csmp.record_failure()
    assert await cb_csmp.before_call() is False
    assert await cb_qc.before_call() is True
    assert await cb_rag.before_call() is True


# ---------------------------------------------------------------------------
# BreakerRegistry（编排层 per-key 复用）
# ---------------------------------------------------------------------------
def test_registry_reuses_same_instance():
    reg = BreakerRegistry(fail_max=3, reset_timeout=30)
    b1 = reg.get_or_create("csmp:llm_degraded:m1")
    b2 = reg.get_or_create("csmp:llm_degraded:m1")
    assert b1 is b2


def test_registry_clear_creates_new_instance():
    reg = BreakerRegistry()
    b1 = reg.get_or_create("csmp:mcp_degraded:rag_kb")
    reg.clear()
    assert reg.get_or_create("csmp:mcp_degraded:rag_kb") is not b1


def test_registry_invalid_params_rejected():
    with pytest.raises(ValueError):
        BreakerRegistry(fail_max=0)
    with pytest.raises(ValueError):
        BreakerRegistry(reset_timeout=-1)

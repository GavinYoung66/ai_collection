"""熔断器状态机原语（per-key 实例，自实现，不依赖 pybreaker）。

状态流转：

    closed ──连续 fail_max 次失败──▶ open ──reset_timeout 秒后──▶ half-open
      ▲                                                              │
      └──────────── 探针成功 ◀─────────────┬────────────── 探针失败──┘
                                          ▼
                                        open（重置计时）

- half-open 只放行恰好 1 个探针请求，其余并发请求按 open 拒绝；
- key 强制带服务前缀与资源标识，禁止全局单 key：
  LLM ``{svc}:llm_degraded:{model}``，MCP ``{svc}:mcp_degraded:{server}``；
- 转为 open 时发 hook 事件 ``{svc}.circuit_breaker.open``。

P0 为进程内状态机；接口全部 async，为后续注入 Redis 多实例权威状态预留。
"""
from __future__ import annotations

import re
import time
from collections.abc import Callable
from enum import Enum

import structlog

from common.resilience.hooks import on_event

logger = structlog.get_logger()

# {svc}:llm_degraded:{model} 或 {svc}:mcp_degraded:{server}
# 资源段非空、不含冒号/空白；裸 "csmp:llm_degraded=1" 这类全局单 key 无法通过。
_BREAKER_KEY_RE = re.compile(
    r"^[a-z0-9_-]+:(?:llm_degraded|mcp_degraded):[^:\s]+$"
)


class BreakerState(str, Enum):
    """熔断器三态：closed 正常放行 / open 跳闸拒绝 / half_open 放唯一探针试探。"""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


def validate_breaker_key(key: str) -> str:
    """校验熔断 key 必须为 {svc}:llm_degraded:{model} / {svc}:mcp_degraded:{server}。"""
    # 步骤1：类型必须是 str
    if not isinstance(key, str) or not _BREAKER_KEY_RE.match(key):
        raise ValueError(
            "熔断 key 必须为 {svc}:llm_degraded:{model} 或 "
            f"{{svc}}:mcp_degraded:{{server}} 格式，实际: {key!r}"
        )
    return key


def make_llm_breaker_key(service: str, model: str) -> str:
    """生成 LLM 类熔断器 key：{服务}:llm_degraded:{模型名}。"""
    return f"{service}:llm_degraded:{model}"


def make_mcp_breaker_key(service: str, server: str) -> str:
    """生成 MCP 类熔断器 key：{服务}:mcp_degraded:{服务端名}。"""
    return f"{service}:mcp_degraded:{server}"


class BreakerRegistry:
    """per-key 熔断器注册表：同 key 复用同一状态机实例（invoke_llm/call_tool 编排层使用）。

    fail_max/reset_timeout 为注册表级默认，注册表内所有 breaker 共享；
    测试用 ``clear()`` 隔离。
    """

    def __init__(self, *, fail_max: int = 5, reset_timeout: int | float = 30) -> None:
        # 步骤1：校验 fail_max 必须 >=1 的整数
        if not isinstance(fail_max, int) or fail_max < 1:
            raise ValueError(f"fail_max 必须为 >=1 的整数，实际: {fail_max!r}")
        # 步骤2：校验 reset_timeout 必须为正数
        if not isinstance(reset_timeout, (int, float)) or reset_timeout <= 0:
            raise ValueError(f"reset_timeout 必须为正数，实际: {reset_timeout!r}")
        # 步骤3：保存默认参数 + 初始化 breaker 字典
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._breakers: dict[str, CircuitBreaker] = {}

    def get_or_create(
        self, key: str, *, clock: Callable[[], float] | None = None
    ) -> CircuitBreaker:
        """按 key 获取已有 breaker，不存在则用注册表默认参数新建。"""
        # 步骤1：先查字典，同 key 复用同一状态机实例
        breaker = self._breakers.get(key)
        if breaker is None:
            # 步骤2：不存在则新建（clock 可选注入，测试用）
            kwargs = {"clock": clock} if clock is not None else {}
            breaker = CircuitBreaker(
                key, fail_max=self._fail_max, reset_timeout=self._reset_timeout, **kwargs
            )
            self._breakers[key] = breaker
        return breaker

    def get(self, key: str) -> CircuitBreaker | None:
        """只读查询 breaker，不存在返回 None。"""
        return self._breakers.get(key)

    def clear(self) -> None:
        """清空注册表（测试隔离用）。"""
        self._breakers.clear()


class CircuitBreaker:
    """per-key 熔断器：构造即校验 key，非法 key fail-fast。"""

    def __init__(
        self,
        key: str,
        fail_max: int = 5,
        reset_timeout: int | float = 30,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # 步骤1：强制校验 key 格式（防全局单 key 事故复发）
        validate_breaker_key(key)
        # 步骤2：校验参数合法性
        if not isinstance(fail_max, int) or fail_max < 1:
            raise ValueError(f"fail_max 必须为 >=1 的整数，实际: {fail_max!r}")
        if not isinstance(reset_timeout, (int, float)) or reset_timeout <= 0:
            raise ValueError(f"reset_timeout 必须为正数，实际: {reset_timeout!r}")
        # 步骤3：保存配置
        self.key = key
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._clock = clock
        # 步骤4：初始化状态机字段
        self._state: BreakerState = BreakerState.CLOSED  # 初始为正常放行
        self._failures = 0                                # 连续失败计数
        self._opened_at: float | None = None              # 跳闸时刻（用于计算 reset_timeout）

    @property
    def state(self) -> str:
        """返回当前状态字符串（closed/open/half_open）。"""
        return self._state.value

    @property
    def failures(self) -> int:
        """返回当前连续失败次数。"""
        return self._failures

    async def before_call(self) -> bool:
        """请求前检查：放行 True（closed 或 half-open 的唯一探针），拒绝 False。"""
        # 分支1：closed → 正常放行
        if self._state is BreakerState.CLOSED:
            return True
        # 分支2：open → 检查是否到了 reset_timeout，可以放探针
        if self._state is BreakerState.OPEN:
            if (
                self._opened_at is not None
                and self._clock() - self._opened_at >= self.reset_timeout
            ):
                # 进入 half-open，本次调用即唯一探针；
                # 状态机此后拒绝其他请求，直到 record_success/record_failure 给出探针结果。
                self._state = BreakerState.HALF_OPEN
                return True
            # 还在跳闸冷却期内，拒绝
            return False
        # 分支3：HALF_OPEN —— 探针已放出，并发请求一律按 open 拒绝
        return False

    async def record_success(self) -> None:
        """记录成功：连续失败计数清零；half-open 探针成功则闭合。"""
        # 步骤1：成功一次就把连续失败计数清零（因为是"连续"失败才算数）
        self._failures = 0
        # 步骤2：如果当前是 half-open 探针且成功 → 恢复 closed
        if self._state is BreakerState.HALF_OPEN:
            self._state = BreakerState.CLOSED
            self._opened_at = None

    async def record_failure(
        self,
        error: BaseException | None = None,
        context: dict | None = None,
    ) -> None:
        """记录失败：closed 累计连续失败达阈值转 open；half-open 探针失败重开并重置计时。

        context 可选透传业务字段（trace_id/node/code），随 open 事件一并发出；
        零件层无请求上下文时可省略（事件至少携带 key/reason）。
        """
        # 分支1：half-open 探针失败 → 立即重开 open 并重置计时
        if self._state is BreakerState.HALF_OPEN:
            self._trip_open()
            await self._emit_open(error, context)
            return
        # 分支2：open 期间的杂散上报（被拒绝的请求）→ 忽略，不影响状态
        if self._state is BreakerState.OPEN:
            return
        # 分支3：closed 正常态 → 连续失败计数 +1
        self._failures += 1
        # 达到阈值 → 跳闸
        if self._failures >= self.fail_max:
            self._trip_open()
            await self._emit_open(error, context)

    def _trip_open(self) -> None:
        """内部：跳闸动作 —— 置 open 状态、记录跳闸时刻、失败计数拉满。"""
        self._state = BreakerState.OPEN
        self._opened_at = self._clock()
        self._failures = self.fail_max

    async def _emit_open(
        self, error: BaseException | None = None, context: dict | None = None
    ) -> None:
        """内部：跳闸时发 hook 事件 {svc}.circuit_breaker.open，携带 key/错误/trace_id。"""
        # 步骤1：从 key 第一段提取服务名，拼事件名
        service = self.key.split(":", 1)[0]
        event_name = f"{service}.circuit_breaker.open"
        # 步骤2：组装事件 payload
        payload: dict = {
            "key": self.key,
            "fail_max": self.fail_max,
            "reason": "circuit_open",
            "error": str(error) if error is not None else None,
        }
        # 步骤3：best-effort 透传业务上下文（trace_id/node/code），空值不覆盖默认字段
        if context:
            for field_name in ("trace_id", "node", "code"):
                value = context.get(field_name)
                if value:
                    payload[field_name] = value
        # 步骤4：发事件；事件上报失败绝不影响熔断主流程（只记 warning）
        try:
            await on_event(event_name, payload)
        except Exception as e:
            logger.warning("breaker_hook_failed", key=self.key, error=str(e))

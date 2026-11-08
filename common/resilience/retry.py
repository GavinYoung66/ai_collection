"""统一重试原语（批次6 / T28）。

仓库内所有重试/退避的“次数、基础间隔、指数因子、上限”只在此文件定义；
调用方通过 ``RetryPolicy`` 或命名策略复用，禁止各自维护魔法数字。

覆盖的四处历史实现：
- ``common/utils/retry.py`` 装饰器
- ``common/llm/reliability`` 的模型调用重试
- ``common/mcp/reliability.py`` 的 MCP 调用重试
- ``common/eventbus/rocketmq.py`` 的消息重试与订阅重连退避

语义约定：``max_attempts`` 为“总调用次数”（含首次），``attempt`` 从 1 开始
表示第几次失败后的退避序号，便于 RocketMQ 的 maxRetryTimes=3（4 次投递）对齐。
"""
from __future__ import annotations

import asyncio
from asyncio import CancelledError as _CancelledError
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from functools import wraps
from typing import TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    """重试配置。``max_attempts`` 含首次调用。"""

    max_attempts: int = 3
    base_delay: float = 1.0
    backoff: float = 2.0
    max_delay: float | None = None

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if self.base_delay < 0:
            raise ValueError("base_delay must be >= 0")
        if self.backoff < 1:
            raise ValueError("backoff must be >= 1")
        if self.max_delay is not None and self.max_delay < 0:
            raise ValueError("max_delay must be >= 0")

    def with_overrides(
        self,
        *,
        max_attempts: int | None = None,
        base_delay: float | None = None,
        backoff: float | None = None,
        max_delay: float | None = None,
    ) -> RetryPolicy:
        return replace(
            self,
            max_attempts=self.max_attempts if max_attempts is None else max_attempts,
            base_delay=self.base_delay if base_delay is None else base_delay,
            backoff=self.backoff if backoff is None else backoff,
            max_delay=self.max_delay if max_delay is None else max_delay,
        )


# ── 唯一配置源：以下策略常量是全仓重试参数的默认值 ──
DEFAULT_RETRY = RetryPolicy(max_attempts=3, base_delay=1.0, backoff=2.0)
LLM_RETRY = RetryPolicy(max_attempts=3, base_delay=1.0, backoff=2.0)
MCP_RETRY = RetryPolicy(max_attempts=3, base_delay=0.5, backoff=2.0)
# RocketMQ 应用可见重试预算：maxRetryTimes=3 → 4 次投递；退避 1s/2s/4s，封顶 8s
EVENTBUS_RETRY = RetryPolicy(max_attempts=4, base_delay=1.0, backoff=2.0, max_delay=8.0)
# 订阅重连退避：1s/2s/4s…封顶 30s
SUBSCRIBE_RETRY = RetryPolicy(max_attempts=1, base_delay=1.0, backoff=2.0, max_delay=30.0)


def retry_delay(attempt: int, policy: RetryPolicy = DEFAULT_RETRY) -> float:
    """第 ``attempt`` 次失败后的退避秒数（attempt 从 1 开始）。"""
    if attempt < 1:
        raise ValueError("attempt must be >= 1")
    delay = policy.base_delay * (policy.backoff ** (attempt - 1))
    if policy.max_delay is not None:
        delay = min(delay, policy.max_delay)
    return delay


def _matches_retryable(
    exc: BaseException, exceptions: tuple[type[BaseException], ...]
) -> bool:
    """T104：沿 ``__cause__``/``__context__`` 链判定可重试异常（spec
    resilience-primitives：重试分类契约——MUST 能识别被转译为业务异常的底层
    可重试错误，MUST NOT 出现"声明 N 次指数退避、实际零重试"的契约失真）。

    函数体把底层可重试错误（如 aiohttp.ClientError/TimeoutError）转译为业务
    异常（``raise BizException(...) from exc``）时，裸 isinstance 匹配不到。
    链首命中即 True，与既有 isinstance 语义完全一致；新增 cause/context 回溯
    仅覆盖显式/隐式转译形态，未转译调用方零行为变化。
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, exceptions):
            return True
        current = current.__cause__ or current.__context__
    return False


async def retry_async(
    fn: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy = DEFAULT_RETRY,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    should_retry: Callable[[BaseException], bool] | None = None,
    on_retry: Callable[[int, BaseException, float], Awaitable[None]] | None = None,
) -> T:
    """执行 ``fn`` 并在可重试异常上按策略退避。

    - ``should_retry`` 返回 False 时立即抛出（如超时/权限错误不重试）；
    - ``on_retry`` 在每次失败后调用，参数为 (第几次失败, 异常, 本次退避秒数)；
      最后一次失败也会回调（delay=0），供事件总线记录 retry 指标；
    - 退避次数耗尽后抛出最后一次异常；
    - 可重试判定沿 cause 链（``_matches_retryable``，T104）：函数体内转译为
      业务异常的底层可重试错误仍按声明执行退避。
    """
    last_error: BaseException | None = None
    for attempt in range(1, policy.max_attempts + 1):
        try:
            return await fn()
        except (KeyboardInterrupt, SystemExit, _CancelledError):
            # 控制流异常永不重试（cause 链匹配也不得吞掉）。CancelledError
            # 取模块加载期快照：本模块 asyncio 允许被测试替换为仅含 sleep
            # 的替身，except 求值不得依赖被替换后的 asyncio 属性（T104 留痕）
            raise
        except BaseException as exc:  # noqa: BLE001 — 匹配下沉 _matches_retryable
            if not _matches_retryable(exc, exceptions):
                raise
            last_error = exc
            exhausted = attempt >= policy.max_attempts
            retryable = should_retry(exc) if should_retry is not None else True
            delay = 0.0 if exhausted or not retryable else retry_delay(attempt, policy)
            if on_retry is not None:
                await on_retry(attempt, exc, delay)
            if not retryable or exhausted:
                raise
            if delay > 0:
                await asyncio.sleep(delay)
    assert last_error is not None  # pragma: no cover — 循环必然返回或抛出
    raise last_error


def retry(
    *,
    max_attempts: int | None = None,
    delay: float | None = None,
    backoff: float | None = None,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
    policy: RetryPolicy = DEFAULT_RETRY,
) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    """异步重试装饰器；未显式传入的参数沿用统一策略默认值。"""
    effective = policy.with_overrides(max_attempts=max_attempts, base_delay=delay, backoff=backoff)

    def decorator(fn: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(fn)
        async def wrapper(*args: object, **kwargs: object) -> T:
            return await retry_async(
                lambda: fn(*args, **kwargs),
                policy=effective,
                exceptions=exceptions,
            )

        return wrapper

    return decorator

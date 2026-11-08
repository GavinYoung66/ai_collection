"""T104：retry_async 沿 cause 链判型（spec resilience-primitives：重试分类契约）。

修复前：speech/callcenter 三处 `@retry(exceptions=(aiohttp.ClientError, TimeoutError))`
的函数体把底层异常转译为 `BizException`（`raise ... from exc`），裸 isinstance
匹配不到 → 声明 3 次指数退避实际零重试（契约失真）。
修复后：`_matches_retryable` 沿 `__cause__`/`__context__` 链判定，转译后的底层
可重试错误仍按声明执行退避；不可重试错误只调用一次；控制流异常永不重试。
"""
from __future__ import annotations

import asyncio

import aiohttp
import pytest

from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException
from common.resilience.retry import RetryPolicy, retry, retry_async


class TestT104TranslatedErrorStillRetries:
    @pytest.mark.asyncio
    async def test_translated_error_still_retries(self):
        """显式转译（raise ... from exc）：底层 TimeoutError 仍按声明执行退避。"""
        calls = {"n": 0}

        @retry(max_attempts=3, delay=0.0, backoff=2.0, exceptions=(asyncio.TimeoutError,))
        async def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                try:
                    raise asyncio.TimeoutError("upstream timeout")
                except asyncio.TimeoutError as exc:
                    raise BizException(ErrorCode.A1013, "语音链路异常") from exc
            return "ok"

        assert await flaky() == "ok"
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_exhausted_raises_translated_exception(self):
        """转译异常重试耗尽：对外类型为转译后的业务异常（不泄漏底层异常）。"""
        calls = {"n": 0}

        @retry(max_attempts=3, delay=0.0, backoff=2.0, exceptions=(asyncio.TimeoutError,))
        async def always_fail():
            calls["n"] += 1
            try:
                raise asyncio.TimeoutError("upstream")
            except asyncio.TimeoutError as exc:
                raise BizException(ErrorCode.A1013) from exc

        with pytest.raises(BizException):
            await always_fail()
        assert calls["n"] == 3

    @pytest.mark.asyncio
    async def test_implicit_context_translation_matches(self):
        """隐式转译（except 块内 raise，无 from）：__context__ 链命中仍重试。"""
        calls = {"n": 0}

        @retry(max_attempts=2, delay=0.0, backoff=2.0, exceptions=(aiohttp.ClientError,))
        async def implicit():
            calls["n"] += 1
            if calls["n"] < 2:
                try:
                    raise aiohttp.ClientError("conn refused")
                except aiohttp.ClientError:
                    raise BizException(ErrorCode.A1013)  # 隐式 __context__
            return "ok"

        assert await implicit() == "ok"
        assert calls["n"] == 2


class TestT104NonRetryableAndControlFlow:
    @pytest.mark.asyncio
    async def test_non_retryable_called_once(self):
        """不可重试错误（cause 链无命中）只调用一次，立即抛出。"""
        calls = {"n": 0}

        @retry(max_attempts=3, delay=0.0, backoff=2.0, exceptions=(asyncio.TimeoutError,))
        async def biz_reject():
            calls["n"] += 1
            raise BizException(ErrorCode.A5002)  # 无底层 cause，不在 exceptions

        with pytest.raises(BizException):
            await biz_reject()
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_cancelled_error_never_retried(self):
        """控制流异常（CancelledError）不因 cause 链匹配被吞掉。"""
        calls = {"n": 0}

        async def cancelled():
            calls["n"] += 1
            try:
                raise asyncio.TimeoutError("inner")
            except asyncio.TimeoutError:
                raise asyncio.CancelledError() from None

        with pytest.raises(asyncio.CancelledError):
            await retry_async(
                cancelled,
                policy=RetryPolicy(max_attempts=3, base_delay=0.0),
                exceptions=(asyncio.TimeoutError,),
            )
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_plain_exception_default_policy_unchanged(self):
        """默认 exceptions=(Exception,) 的既有语义回归：链首命中，行为不变。"""
        calls = {"n": 0}

        @retry(max_attempts=2, delay=0.0, backoff=2.0)
        async def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise RuntimeError("generic")
            return "ok"

        assert await flaky() == "ok"
        assert calls["n"] == 2
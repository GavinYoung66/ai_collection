"""common.utils 模块单元测试。"""
import asyncio

import pytest

from common.utils.retry import retry
from common.utils.time_guard import check_call_time
from common.utils.semaphore import CallSemaphore
from common.config.settings import settings
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


class TestRetry:
    @pytest.mark.asyncio()
    async def test_success_no_retry(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        async def func():
            nonlocal call_count
            call_count += 1
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 1

    @pytest.mark.asyncio()
    async def test_retry_then_success(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        async def func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ValueError("retry")
            return "ok"

        result = await func()
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio()
    async def test_retry_exhausted(self):
        call_count = 0

        @retry(max_attempts=2, delay=0.01, exceptions=(ValueError,))
        async def func():
            nonlocal call_count
            call_count += 1
            raise ValueError("always fail")

        with pytest.raises(ValueError, match="always fail"):
            await func()
        assert call_count == 2

    @pytest.mark.asyncio()
    async def test_retry_only_specified_exception(self):
        call_count = 0

        @retry(max_attempts=3, delay=0.01, exceptions=(ValueError,))
        async def func():
            nonlocal call_count
            call_count += 1
            raise TypeError("wrong type")

        with pytest.raises(TypeError):
            await func()
        assert call_count == 1


class TestTimeGuard:
    def test_check_call_time_within_hours(self, monkeypatch):
        from datetime import datetime
        fake_time = datetime(2026, 1, 15, 10, 30, 0)
        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_time
        monkeypatch.setattr("common.utils.time_guard.datetime", FakeDateTime)
        check_call_time()

    def test_check_call_time_outside_hours(self, monkeypatch):
        from datetime import datetime
        fake_time = datetime(2026, 1, 15, 22, 0, 0)
        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_time
        monkeypatch.setattr("common.utils.time_guard.datetime", FakeDateTime)
        with pytest.raises(BizException) as exc_info:
            check_call_time()
        assert exc_info.value.code == ErrorCode.A1001

    def test_check_call_time_boundary_start(self, monkeypatch):
        from datetime import datetime
        fake_time = datetime(2026, 1, 15, 8, 0, 0)
        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_time
        monkeypatch.setattr("common.utils.time_guard.datetime", FakeDateTime)
        check_call_time()

    def test_check_call_time_boundary_end(self, monkeypatch):
        from datetime import datetime
        fake_time = datetime(2026, 1, 15, 21, 0, 0)
        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return fake_time
        monkeypatch.setattr("common.utils.time_guard.datetime", FakeDateTime)
        with pytest.raises(BizException) as exc_info:
            check_call_time()
        assert exc_info.value.code == ErrorCode.A1001


class TestSemaphore:
    @pytest.mark.asyncio()
    async def test_acquire_and_release(self):
        sem = CallSemaphore(max_concurrency=2)
        await sem.acquire()
        await sem.acquire()
        sem.release()
        sem.release()

    @pytest.mark.asyncio()
    async def test_concurrent_limit(self):
        sem = CallSemaphore(max_concurrency=2)
        active = 0
        max_active = 0

        async def worker():
            nonlocal active, max_active
            async with sem:
                active += 1
                max_active = max(max_active, active)
                await asyncio.sleep(0.02)
                active -= 1

        await asyncio.gather(*[worker() for _ in range(5)])
        assert max_active <= 2

    @pytest.mark.asyncio()
    async def test_max_concurrency_50_default(self):
        sem = CallSemaphore()
        assert sem._semaphore._value == settings.CALL_MAX_CONCURRENCY

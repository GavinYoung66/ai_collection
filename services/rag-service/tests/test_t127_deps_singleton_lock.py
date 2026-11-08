"""T127：deps 惰性单例 getter 加锁（DCL）并发安全测试。

spec platform/runtime-correctness「惰性单例初始化线程安全」验收：
- 100 个并发请求同时首次调用同一 getter，构造函数只被调用一次；
- 并发检索信号量上限不超配置：get_llm_service 单例唯一 → LLMService
  内部 asyncio.Semaphore 全进程唯一，不再 ×N 放大。

盘点口径：现场共 13 个惰性构造型单例 getter（5 个同步 def + 8 个
async def，含 get_redis；get_db 为依赖注入生成器不计入）。
"""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock

import pytest

from app.api import deps
from app.services.llm_service import LLMService
from app.settings import settings

# 全量 getter 盘点清单：(getter 函数名, 模块级单例变量, 模块内构造类名)
_SYNC_GETTERS = [
    ("get_milvus_client", "_milvus_client", "MilvusClient"),
    ("get_es_client", "_es_client", "ESClient"),
    ("get_routing_service", "_routing_service", "RoutingService"),
    ("get_weighting_service", "_weighting_service", "WeightingService"),
    ("get_row_access_service", "_row_access_service", "RowAccessService"),
]
_ASYNC_GETTERS = [
    ("get_redis", "_redis_client", "Redis"),
    ("get_embed_client", "_embed_client", "EmbedClient"),
    ("get_rerank_client", "_rerank_client", "RerankClient"),
    ("get_ocr_client", "_ocr_client", "OCRClient"),
    ("get_asr_client", "_asr_client", "ASRClient"),
    ("get_rewrite_service", "_rewrite_service", "RewriteService"),
    ("get_session_service", "_session_service", "SessionService"),
]


def _make_counting_cls(calls: dict):
    """生成计数构造替身：__init__ 每执行一次 calls["n"] += 1（线程安全）。"""
    lock = threading.Lock()

    class _Counting:
        def __init__(self, *args, **kwargs):
            with lock:
                calls["n"] += 1

    return _Counting


def _run_concurrent_sync(fn, workers: int = 100) -> list:
    """workers 个线程 barrier 对齐后同时首调 fn（最大化 check-then-act 竞态窗口）。"""
    barrier = threading.Barrier(workers)
    results: list = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        out = fn()
        with results_lock:
            results.append(out)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker) for _ in range(workers)]
        for f in futures:
            f.result()
    return results


async def _run_concurrent_gather(fn, n: int = 100) -> list:
    """n 个协程并发首调 fn（模拟 100 个并发请求同时进入 getter）。"""
    return list(await asyncio.gather(*[fn() for _ in range(n)]))


def _run_concurrent_async_cross_thread(coro_fn, workers: int = 32) -> list:
    """workers 个线程各自 asyncio.run 独立 loop 并发首调 async getter。

    该形态下模块级 asyncio.Lock 必然抛 RuntimeError（跨 loop 绑定），
    threading.Lock 方案则稳定单例——同时用于验证加锁形态裁决。
    """
    barrier = threading.Barrier(workers)
    results: list = []
    errors: list = []
    results_lock = threading.Lock()

    def _worker():
        barrier.wait()
        try:
            out = asyncio.run(coro_fn())
            with results_lock:
                results.append(out)
        except Exception as e:  # pragma: no cover - 竞态失败路径
            with results_lock:
                errors.append(e)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_worker) for _ in range(workers)]
        for f in futures:
            f.result()
    assert errors == []
    return results


# ---------- 同步 getter：线程级并发单实例 ----------


class TestSyncGetterConcurrency:
    @pytest.mark.parametrize("getter_name,attr,ctor_name", _SYNC_GETTERS)
    def test_concurrent_first_call_single_instance(
        self, monkeypatch, getter_name, attr, ctor_name
    ):
        """100 线程同时首次调用同一同步 getter，构造函数仅执行一次。"""
        calls = {"n": 0}
        monkeypatch.setattr(deps, attr, None)
        monkeypatch.setattr(deps, ctor_name, _make_counting_cls(calls))
        getter = getattr(deps, getter_name)

        results = _run_concurrent_sync(getter, workers=100)

        assert calls["n"] == 1, f"{getter_name} 并发首调构造了 {calls['n']} 次"
        assert all(r is results[0] for r in results)


# ---------- async getter：协程并发 + 跨线程真竞态 ----------


class TestAsyncGetterConcurrency:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("getter_name,attr,ctor_name", _ASYNC_GETTERS)
    async def test_gather_first_call_single_instance(
        self, monkeypatch, getter_name, attr, ctor_name
    ):
        """100 协程（asyncio.gather）同时首次调用同一 async getter，仅构造一次。"""
        calls = {"n": 0}
        monkeypatch.setattr(deps, attr, None)
        monkeypatch.setattr(deps, ctor_name, _make_counting_cls(calls))
        if getter_name != "get_redis":
            # 其余 async getter 依赖 await get_redis()：patch 为固定替身，不真构造 Redis
            sentinel = object()

            async def _fake_redis():
                return sentinel

            monkeypatch.setattr(deps, "get_redis", _fake_redis)
        getter = getattr(deps, getter_name)

        results = await _run_concurrent_gather(getter, n=100)

        assert calls["n"] == 1, f"{getter_name} 并发首调构造了 {calls['n']} 次"
        assert all(r is results[0] for r in results)

    @pytest.mark.parametrize(
        "getter_name,attr,ctor_name",
        [("get_redis", "_redis_client", "Redis"),
         ("get_embed_client", "_embed_client", "EmbedClient")],
    )
    def test_cross_thread_real_race_single_instance(
        self, monkeypatch, getter_name, attr, ctor_name
    ):
        """跨线程真竞态（每线程独立 loop）：threading.Lock 保证仅构造一次。

        修复前无锁 check-then-act 在此形态下大概率构造多次；若误用
        asyncio.Lock，跨 loop 复用将直接 RuntimeError。
        """
        calls = {"n": 0}
        monkeypatch.setattr(deps, attr, None)
        monkeypatch.setattr(deps, ctor_name, _make_counting_cls(calls))
        if getter_name != "get_redis":
            sentinel = object()

            async def _fake_redis():
                return sentinel

            monkeypatch.setattr(deps, "get_redis", _fake_redis)
        coro_fn = getattr(deps, getter_name)

        results = _run_concurrent_async_cross_thread(coro_fn, workers=32)

        assert calls["n"] == 1, f"{getter_name} 跨线程首调构造了 {calls['n']} 次"
        assert all(r is results[0] for r in results)


# ---------- get_llm_service：单例唯一 + 信号量上限等于配置 ----------


class TestLLMServiceSingleton:
    @pytest.mark.asyncio
    async def test_100_concurrent_same_instance(self, monkeypatch):
        """100 协程并发获取 get_llm_service：同一实例（is 断言）+ 仅构造一次。"""
        calls = {"n": 0}
        sentinel = object()

        async def _fake_redis():
            return sentinel

        class _CountingLLM(_make_counting_cls(calls)):
            pass

        monkeypatch.setattr(deps, "_llm_service", None)
        monkeypatch.setattr(deps, "get_redis", _fake_redis)
        monkeypatch.setattr(deps, "LLMService", _CountingLLM)

        results = await _run_concurrent_gather(deps.get_llm_service, n=100)

        assert calls["n"] == 1
        assert all(r is results[0] for r in results)

    def test_cross_thread_same_instance(self, monkeypatch):
        """32 线程（独立 loop）并发获取 get_llm_service：单例唯一，构造一次。"""
        calls = {"n": 0}
        sentinel = object()

        async def _fake_redis():
            return sentinel

        monkeypatch.setattr(deps, "_llm_service", None)
        monkeypatch.setattr(deps, "get_redis", _fake_redis)
        monkeypatch.setattr(deps, "LLMService", _make_counting_cls(calls))

        results = _run_concurrent_async_cross_thread(deps.get_llm_service, workers=32)

        assert calls["n"] == 1
        assert all(r is results[0] for r in results)

    @pytest.mark.asyncio
    async def test_semaphore_limit_equals_config(self, monkeypatch):
        """信号量上限断言：LLMService 实例 _sem._value == LLM_MAX_CONCURRENCY。

        T127 留痕：LLMService.__init__ 内 self._sem =
        asyncio.Semaphore(settings.LLM_MAX_CONCURRENCY)；单例唯一后
        全进程仅一个 _sem，并发检索信号量上限不超配置（不再 ×N）。
        """
        sentinel = object()

        async def _fake_redis():
            return sentinel

        monkeypatch.setattr(deps, "_llm_service", None)
        monkeypatch.setattr(deps, "get_redis", _fake_redis)

        svc = await deps.get_llm_service()
        assert isinstance(svc, LLMService)
        # 直接构造对照：信号量初值即配置上限
        direct = LLMService(redis=MagicMock())
        assert svc._sem._value == settings.LLM_MAX_CONCURRENCY
        assert direct._sem._value == settings.LLM_MAX_CONCURRENCY
        # 单例唯一 → 全进程共享同一信号量实例
        again = await deps.get_llm_service()
        assert again is svc
        assert again._sem is svc._sem


# ---------- 既有注入形态兼容（monkeypatch 直改模块级变量） ----------


class TestStubInjectionCompatibility:
    def test_sync_stub_returned_without_construction(self, monkeypatch):
        """同步 getter：模块级变量被 monkeypatch 为替身时直接返回替身，不进锁构造。"""
        stub = object()
        calls = {"n": 0}
        monkeypatch.setattr(deps, "_milvus_client", stub)
        monkeypatch.setattr(deps, "MilvusClient", _make_counting_cls(calls))

        assert deps.get_milvus_client() is stub
        assert calls["n"] == 0

        # 复位为 None（既有测试 reset 形态）后恢复正常惰性构造
        monkeypatch.setattr(deps, "_milvus_client", None)
        built = deps.get_milvus_client()
        assert built is not stub
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_async_stub_returned_without_construction(self, monkeypatch):
        """async getter：替身注入直接返回；get_redis 不被触发。"""
        stub = object()
        calls = {"n": 0}
        redis_calls = {"n": 0}

        async def _fail_redis():
            redis_calls["n"] += 1
            raise AssertionError("替身注入时不应触发 get_redis")

        monkeypatch.setattr(deps, "_embed_client", stub)
        monkeypatch.setattr(deps, "get_redis", _fail_redis)
        monkeypatch.setattr(deps, "EmbedClient", _make_counting_cls(calls))

        assert await deps.get_embed_client() is stub
        assert calls["n"] == 0
        assert redis_calls["n"] == 0

    def test_reset_then_rebuild_new_instance(self, monkeypatch):
        """单例复位（_xxx = None）后再次首调重建全新实例（A is not B）。"""
        calls = {"n": 0}
        monkeypatch.setattr(deps, "_es_client", None)
        monkeypatch.setattr(deps, "ESClient", _make_counting_cls(calls))

        first = deps.get_es_client()
        monkeypatch.setattr(deps, "_es_client", None)
        second = deps.get_es_client()

        assert calls["n"] == 2
        assert first is not second
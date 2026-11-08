"""T107：qc rag_client 词库熔断收敛 common CircuitBreaker（半开恢复 + health 如实）。

spec：platform/resilience-primitives「熔断恢复与健康如实」——
- 冷却期后首次试探成功 → 熔断关闭、失败计数清零；
- 熔断打开时健康探针如实返回不连通（MUST NOT 报告 connected）；
- 失败计数可清零（成功即清零）。
衔接：WordLoader.load_once 不再前置 breaker_open 短路，半开探针经生产主链放行。
"""
import httpx
import pytest

from app.integrations.rag_client import (
    RAG_BREAKER_FAILURES,
    RAG_DICT_BREAKER_KEY,
    RAG_DICT_BREAKER_RESET_TIMEOUT,
    RagClient,
)
from app.repositories.job_store import QcJobStore
from app.services.ac_filter import ACFilter, SensitiveWord
from app.services.word_loader import WordLoader
from tests.test_job_store import MemoryRedis


def _clock_at(start: float):
    now = {"t": start}
    return now, (lambda: now["t"])


def _client(handler, clock) -> RagClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
    return RagClient(
        base_url="http://rag-service:8002", retry_delay=0, client=http, breaker_clock=clock
    )


def _failing_handler(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectTimeout("rag dict timeout")


async def _trip_open(client: RagClient) -> None:
    """连续 RAG_BREAKER_FAILURES 轮失败（每轮内重试 RAG_RETRY_ATTEMPTS 次）→ open。"""
    for _ in range(RAG_BREAKER_FAILURES):
        await client.fetch_current_dict()
    assert client._dict_breaker.state == "open"


class TestT107HalfOpenRecovery:
    @pytest.mark.asyncio
    async def test_half_open_probe_success_closes_and_clears_failures(self):
        now, clock = _clock_at(1000.0)
        state = {"ok": False}

        def handler(request: httpx.Request) -> httpx.Response:
            if not state["ok"]:
                raise httpx.ConnectTimeout("rag dict timeout")
            return httpx.Response(200, text="新词\n")

        client = _client(handler, clock)
        client._words = ["旧词"]
        client._ac_ref = ("旧词",)
        await _trip_open(client)
        assert client.breaker_open is True
        assert client.fetch_healthy is False

        now["t"] += RAG_DICT_BREAKER_RESET_TIMEOUT  # 冷却期满 → 半开探针放行
        state["ok"] = True
        words = await client.fetch_current_dict()

        assert words == ["新词"]
        assert client.ac_ref == ("新词",)
        assert client._dict_breaker.state == "closed"
        assert client._dict_breaker.failures == 0
        assert client.breaker_open is False
        assert client.fetch_healthy is True

    @pytest.mark.asyncio
    async def test_half_open_probe_failure_reopens_and_resets_timer(self):
        now, clock = _clock_at(1000.0)
        client = _client(_failing_handler, clock)
        await _trip_open(client)

        now["t"] += RAG_DICT_BREAKER_RESET_TIMEOUT
        words = await client.fetch_current_dict()  # 半开探针失败 → 重开并重置计时
        assert words == []
        assert client._dict_breaker.state == "open"
        assert client.breaker_open is True
        urls_after_reopen = list(client.recorded_urls())

        now["t"] += RAG_DICT_BREAKER_RESET_TIMEOUT - 1  # 重开后新计时未满 → 仍拒绝
        again = await client.fetch_current_dict()
        assert again == []
        assert list(client.recorded_urls()) == urls_after_reopen  # 未发起 GET

        now["t"] += 1  # 冷却再次期满 → 探针重新放行（重置计时语义成立）
        assert await client._dict_breaker.before_call() is True


class TestT107ProbeHonesty:
    @pytest.mark.asyncio
    async def test_probe_returns_false_while_open_without_http(self):
        now, clock = _clock_at(1000.0)
        client = _client(_failing_handler, clock)
        await _trip_open(client)
        urls_before = list(client.recorded_urls())

        assert await client.probe_dict_reachable() is False
        assert list(client.recorded_urls()) == urls_before  # open 时不发起 GET

    @pytest.mark.asyncio
    async def test_probe_returns_false_in_half_open_state(self):
        now, clock = _clock_at(1000.0)
        client = _client(_failing_handler, clock)
        await _trip_open(client)
        now["t"] += RAG_DICT_BREAKER_RESET_TIMEOUT
        assert await client._dict_breaker.before_call() is True  # 探针放出 → half_open
        assert await client.probe_dict_reachable() is False  # 探针未决 → 如实不连通

    @pytest.mark.asyncio
    async def test_probe_true_when_closed(self, monkeypatch):
        # probe 内部自建 httpx.AsyncClient 真连（不读注入 client）——
        # patch 构造使其走 MockTransport，验证 closed 态探测成功语义。
        _now, clock = _clock_at(1000.0)
        client = _client(_failing_handler, clock)
        real_async_client = httpx.AsyncClient

        def patched_async_client(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(200, text="起诉\n")
            )
            return real_async_client(*args, **kwargs)

        monkeypatch.setattr(httpx, "AsyncClient", patched_async_client)
        assert await client.probe_dict_reachable() is True


class TestT107FailureCounterReset:
    @pytest.mark.asyncio
    async def test_success_clears_failure_counter(self):
        hits = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            hits["n"] += 1
            if hits["n"] <= 3:
                raise httpx.ConnectTimeout("rag dict timeout")
            return httpx.Response(200, text="起诉\n")

        _now, clock = _clock_at(1000.0)
        client = _client(handler, clock)
        await client.fetch_current_dict()
        assert client._dict_breaker.failures == 1
        assert client._dict_breaker.state == "closed"

        words = await client.fetch_current_dict()
        assert words == ["起诉"]
        assert client._dict_breaker.failures == 0
        assert client.fetch_healthy is True

    def test_dict_breaker_key_contract(self):
        assert RAG_DICT_BREAKER_KEY == "qc:mcp_degraded:rag_dict"
        assert RAG_BREAKER_FAILURES == 2
        assert RAG_DICT_BREAKER_RESET_TIMEOUT > 0


class TestT107WordLoaderRecoveryPath:
    @pytest.mark.asyncio
    async def test_half_open_recovery_through_load_once(self):
        now, clock = _clock_at(1000.0)
        state = {"ok": True}

        def handler(request: httpx.Request) -> httpx.Response:
            if not state["ok"]:
                raise httpx.ConnectTimeout("rag dict timeout")
            return httpx.Response(200, text="起诉\n立案\n")

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        ac = ACFilter()
        await ac.reload([SensitiveWord(word="旧词")])
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        rag = RagClient(
            base_url="http://rag-service:8002",
            retry_delay=0,
            client=http,
            breaker_clock=clock,
        )
        loader = WordLoader(rag=rag, ac=ac, job_store=store)

        first = await loader.load_once(event_version="v1")
        assert first["ok"] is True
        assert await store.get_words_version() == "v1"

        state["ok"] = False
        for _ in range(RAG_BREAKER_FAILURES):
            failed = await loader.load_once(event_version="v2")
            assert failed["ok"] is False
            assert failed["kept_old"] is True
        assert rag.breaker_open is True

        again = await loader.load_once(event_version="v3")  # open 冷却期内仍短路保旧
        assert again["ok"] is False
        assert again["kept_old"] is True
        assert await store.get_words_version() == "v1"
        assert not ac.match("旧词", "agent")
        assert ac.match("起诉", "agent")

        now["t"] += RAG_DICT_BREAKER_RESET_TIMEOUT  # 冷却期满 → 半开探针经主链放行
        state["ok"] = True
        recovered = await loader.load_once(event_version="v4")
        assert recovered["ok"] is True
        assert await store.get_words_version() == "v4"
        assert rag.breaker_open is False
        assert rag.fetch_healthy is True
        assert ac.match("起诉", "agent")
        assert ac.match("立案", "agent")
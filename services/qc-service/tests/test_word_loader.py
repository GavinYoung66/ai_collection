"""QC-7.3：WordLoader 全量 current.dict；写 qc:words:version；失败保旧词表。"""
from __future__ import annotations

import inspect

import httpx
import pytest

from app.events.subscriber import QcEventSubscriber
from app.integrations.rag_client import DICT_PATH, RAG_BREAKER_FAILURES, RagClient
from app.observability import captured_events, reset_events
from app.repositories.job_store import KEY_WORDS_VERSION, QcJobStore
from app.services.ac_filter import ACFilter, SensitiveWord
from app.services.word_loader import HOURLY_INTERVAL_SECONDS, WordLoader
from tests.test_job_store import MemoryRedis


def _assert_no_search(urls: list[str]) -> None:
    joined = " ".join(urls)
    assert "since_v" not in joined
    assert "/kb/search" not in joined
    assert "/api/v1/kb/search" not in joined


def _rag(handler) -> RagClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
    return RagClient(base_url="http://rag-service:8002", retry_delay=0, client=http)


class TestWordLoader:
    def test_hourly_is_3600_and_client_has_no_since_v(self):
        assert HOURLY_INTERVAL_SECONDS == 3600.0
        src = inspect.getsource(WordLoader)
        assert "since_v" not in src
        assert "/kb/search" not in src
        assert not hasattr(RagClient, "search")

    @pytest.mark.asyncio
    async def test_load_once_writes_words_version(self):
        reset_events()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url).endswith(DICT_PATH)
            assert "since_v" not in str(request.url)
            return httpx.Response(200, text="起诉\n立案\n")

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        ac = ACFilter()
        rag = _rag(handler)
        loader = WordLoader(rag=rag, ac=ac, job_store=store)
        out = await loader.load_once()
        assert out["ok"] is True
        assert await store.get_words_version() == "full:2"
        assert redis.data[KEY_WORDS_VERSION] == "full:2"
        assert ac.match("起诉", "agent")
        loaded = [e for e in captured_events() if e["event"] == "rag.words.loaded"]
        assert loaded
        assert loaded[-1]["version"] == "full:2"
        _assert_no_search(list(rag.recorded_urls()))

    @pytest.mark.asyncio
    async def test_kb_sensitive_updated_writes_event_version(self):
        reset_events()
        hits = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            hits["n"] += 1
            return httpx.Response(200, text="强制执行\n")

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        ac = ACFilter()
        rag = _rag(handler)
        loader = WordLoader(rag=rag, ac=ac, job_store=store)
        await loader.load_once()
        sub = QcEventSubscriber(word_loader=loader, job_store=store)
        out = await sub.handle_kb_updated({"version": "v20260910", "trace_id": "t", "ts": 1})
        assert out["ok"] is True
        assert await store.get_words_version() == "v20260910"
        assert hits["n"] == 2
        _assert_no_search(list(rag.recorded_urls()))
        assert all("since_v" not in url for url in rag.recorded_urls())

    @pytest.mark.asyncio
    async def test_breaker_keeps_old_ac_and_version(self):
        state = {"ok": True}

        def handler(request: httpx.Request) -> httpx.Response:
            if not state["ok"]:
                raise httpx.ConnectTimeout("rag dict timeout")
            return httpx.Response(200, text="起诉\n")

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        ac = ACFilter()
        await ac.reload([SensitiveWord(word="旧词")])
        rag = _rag(handler)
        loader = WordLoader(rag=rag, ac=ac, job_store=store)
        first = await loader.load_once(event_version="v1")
        assert first["ok"] is True
        assert await store.get_words_version() == "v1"
        assert ac.match("起诉", "agent")
        state["ok"] = False
        for _ in range(RAG_BREAKER_FAILURES):
            failed = await loader.load_once(event_version="v2")
            assert failed["ok"] is False
            assert failed["kept_old"] is True
        assert rag.breaker_open is True
        again = await loader.load_once(event_version="v3")
        assert again["ok"] is False
        assert again["kept_old"] is True
        assert await store.get_words_version() == "v1"
        assert ac.match("起诉", "agent")
        assert not ac.match("旧词", "agent")
        _assert_no_search(list(rag.recorded_urls()))
        assert all("since_v" not in url and "/kb/search" not in url for url in rag.recorded_urls())

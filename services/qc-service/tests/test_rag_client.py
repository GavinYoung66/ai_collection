"""QC-3.1：rag 只拉 current.dict；无 search；失败保持旧词表。"""
from __future__ import annotations

import inspect

import httpx
import pytest

from app.core.config import qc_settings
from app.integrations.rag_client import DICT_PATH, RAG_BREAKER_FAILURES, RAG_RETRY_ATTEMPTS, RagClient


def _assert_no_search(urls: list[str]) -> None:
    joined = " ".join(urls)
    assert "/kb/search" not in joined
    assert "/api/v1/kb/search" not in joined


class TestRagClientSurface:
    def test_no_search_method(self):
        assert not hasattr(RagClient, "search")
        assert not hasattr(RagClient, "kb_search")
        names = [name for name, _ in inspect.getmembers(RagClient, predicate=inspect.isfunction)]
        assert not any("search" in name.lower() for name in names)
        assert "search" not in vars(RagClient)

    def test_r14_flag_stays_false(self):
        assert qc_settings.QC_R14_RAG_ENABLED is False

    def test_timeout_default_is_3s(self):
        client = RagClient()
        assert client._timeout == 3
        assert client.dict_url().endswith(DICT_PATH)
        assert "/api/v1" not in qc_settings.QC_RAG_SENSITIVE_BASE


class TestRagClientFetch:
    @pytest.mark.asyncio
    async def test_loads_plain_text_dict(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.method == "GET"
            assert str(request.url).endswith("/api/v1/sensitive-words/current.dict")
            assert request.headers.get("Accept") == "text/plain"
            return httpx.Response(200, text="起诉\n立案\n# comment\n\n强制执行\n")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8002", retry_delay=0, client=http)
        words = await client.fetch_current_dict()
        assert words == ["起诉", "立案", "强制执行"]
        assert client.ac_ref == ("起诉", "立案", "强制执行")
        _assert_no_search(list(client.recorded_urls()))
        assert all(method == "GET" for method, _url in client.recorded_requests())

    @pytest.mark.asyncio
    async def test_timeout_retries_three_times_keeps_old_words(self):
        hits = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            hits["n"] += 1
            raise httpx.ConnectTimeout("rag dict timeout")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8002", retry_delay=0, client=http)
        client._words = ["起诉"]
        client._ac_ref = ("起诉",)
        old_ref = client.ac_ref

        words = await client.fetch_current_dict()
        assert words == ["起诉"]
        assert client.ac_ref is old_ref
        assert hits["n"] == RAG_RETRY_ATTEMPTS
        _assert_no_search(list(client.recorded_urls()))

    @pytest.mark.asyncio
    async def test_two_consecutive_failures_keep_old_ac_and_never_search(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("rag dict timeout")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8002", retry_delay=0, client=http)
        client._words = ["立案"]
        client._ac_ref = ("立案",)
        old_ref = client.ac_ref

        first = await client.fetch_current_dict()
        second = await client.fetch_current_dict()
        assert first == ["立案"]
        assert second == ["立案"]
        assert client.ac_ref is old_ref
        assert client._consecutive_failures == RAG_BREAKER_FAILURES
        assert client.breaker_open is True
        _assert_no_search(list(client.recorded_urls()))
        assert all("current.dict" in url for url in client.recorded_urls())
        assert all(method == "GET" for method, _url in client.recorded_requests())

    @pytest.mark.asyncio
    async def test_open_breaker_skips_further_get(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout("rag dict timeout")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8002", retry_delay=0, client=http)
        client._words = ["立案"]
        client._ac_ref = ("立案",)
        old_ref = client.ac_ref

        await client.fetch_current_dict()
        await client.fetch_current_dict()
        assert client.breaker_open is True
        urls_when_open = list(client.recorded_urls())
        assert len(urls_when_open) == RAG_BREAKER_FAILURES * RAG_RETRY_ATTEMPTS

        third = await client.fetch_current_dict()
        assert third == ["立案"]
        assert client.ac_ref is old_ref
        assert list(client.recorded_urls()) == urls_when_open
        _assert_no_search(list(client.recorded_urls()))

    @pytest.mark.asyncio
    async def test_success_clears_breaker(self):
        hits = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            hits["n"] += 1
            if hits["n"] <= RAG_RETRY_ATTEMPTS:
                raise httpx.ConnectTimeout("rag dict timeout")
            return httpx.Response(200, text="起诉\n")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8002", retry_delay=0, client=http)
        client._words = ["立案"]
        client._ac_ref = ("立案",)

        first = await client.fetch_current_dict()
        assert first == ["立案"]
        assert client.breaker_open is False
        assert client._consecutive_failures == 1

        second = await client.fetch_current_dict()
        assert second == ["起诉"]
        assert client.ac_ref == ("起诉",)
        assert client._consecutive_failures == 0
        assert client.breaker_open is False

"""QC-3.1：rag GET current.dict；search 独立于词库熔断。"""
from __future__ import annotations

import inspect
import json

import httpx
import pytest

from app.core.config import qc_settings
from app.integrations.rag_client import DICT_PATH, RAG_BREAKER_FAILURES, RAG_RETRY_ATTEMPTS, RagClient


def _assert_no_search(urls: list[str]) -> None:
    joined = " ".join(urls)
    assert "/kb/search" not in joined
    assert "/api/v1/kb/search" not in joined


class TestRagClientSurface:
    def test_search_method_exists(self):
        assert hasattr(RagClient, "search")
        names = [name for name, _ in inspect.getmembers(RagClient, predicate=inspect.isfunction)]
        assert "search" in names or "search" in vars(RagClient)

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


class TestRagClientSearch:
    @pytest.mark.asyncio
    async def test_search_posts_kb_path_and_returns_snippet(self):
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["method"] = request.method
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content.decode("utf-8"))
            seen["trace"] = request.headers.get("X-Trace-Id")
            return httpx.Response(
                200,
                json={
                    "code": "0",
                    "data": {
                        "results": [
                            {
                                "content": "公约59严禁虚构免息、全额减免。" + ("补" * 80),
                                "score": 0.81,
                            }
                        ]
                    },
                },
            )

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8000", retry_delay=0, client=http)
        snippet = await client.search("再不还就起诉你")
        assert seen["method"] == "POST"
        assert str(seen["url"]).endswith("/api/v1/kb/search")
        assert "/api/v1" not in "http://rag-service:8000"
        body = seen["body"]
        assert body == {
            "query": "再不还就起诉你",
            "category": "script",
            "top_k": 3,
            "answer": False,
        }
        assert snippet is not None
        assert snippet.startswith("公约59严禁虚构免息")
        assert len(snippet) == 80
        assert ("POST", client.search_url()) in client.recorded_requests()

    @pytest.mark.asyncio
    async def test_search_timeout_does_not_trip_dict_breaker(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "POST":
                raise httpx.ReadTimeout("search timeout")
            return httpx.Response(200, text="起诉\n")

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8000", retry_delay=0, client=http)
        client._words = ["起诉"]
        client._ac_ref = ("起诉",)
        snippet = await client.search("再不还就起诉你")
        assert snippet is None
        assert client._consecutive_failures == 0
        assert client.breaker_open is False
        words = await client.fetch_current_dict()
        assert words == ["起诉"]

    @pytest.mark.asyncio
    async def test_empty_results_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"results": []})

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=3.0)
        client = RagClient(base_url="http://rag-service:8000", retry_delay=0, client=http)
        assert await client.search("强制执行") is None


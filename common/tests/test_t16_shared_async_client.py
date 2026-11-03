"""T16：LLM 共享 AsyncClient 复用单测。

验收口径：同一事件循环内 50 次调用只创建 1 个 client；显式传入 client 不纳入
共享池；共享 client 关闭后按需重建；不同事件循环隔离。
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from common.llm import transport as transport_mod
from common.llm.transport import aclose_shared_client, post_json


def _handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"ok": True})


def _fail_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, text="boom")


@pytest.fixture(autouse=True)
def _reset_shared_clients():
    transport_mod._shared_clients.clear()
    yield
    transport_mod._shared_clients.clear()


@pytest.fixture
def client_factory(monkeypatch):
    """记录 AsyncClient 构造次数，并让每次构造都带 MockTransport。"""
    created: list[httpx.AsyncClient] = []
    real_cls = httpx.AsyncClient

    def factory(*args, **kwargs):
        client = real_cls(transport=httpx.MockTransport(_handler))
        created.append(client)
        return client

    monkeypatch.setattr(transport_mod.httpx, "AsyncClient", factory)
    return SimpleNamespace(created=created, real_cls=real_cls)


async def _call() -> dict:
    return await post_json(
        url="https://example.com/v1/run",
        api_key="sk-test",
        payload={"model": "m"},
    )


@pytest.mark.asyncio
async def test_50_calls_reuse_single_client(client_factory):
    for _ in range(50):
        assert await _call() == {"ok": True}
    assert len(client_factory.created) == 1
    assert transport_mod._shared_clients[asyncio.get_running_loop()] is client_factory.created[0]
    await aclose_shared_client()
    assert client_factory.created[0].is_closed


@pytest.mark.asyncio
async def test_explicit_client_bypasses_shared_pool(client_factory):
    async with client_factory.real_cls(transport=httpx.MockTransport(_handler)) as own:
        assert await post_json(
            url="https://example.com/v1/run",
            api_key="sk-test",
            payload={},
            client=own,
        ) == {"ok": True}
    assert client_factory.created == []
    assert len(transport_mod._shared_clients) == 0


@pytest.mark.asyncio
async def test_error_path_does_not_create_extra_client(monkeypatch):
    created: list[httpx.AsyncClient] = []
    real_cls = httpx.AsyncClient

    def factory(*args, **kwargs):
        client = real_cls(transport=httpx.MockTransport(_fail_handler))
        created.append(client)
        return client

    monkeypatch.setattr(transport_mod.httpx, "AsyncClient", factory)
    for _ in range(5):
        with pytest.raises(RuntimeError, match="LLM HTTP 500"):
            await _call()
    assert len(created) == 1
    await aclose_shared_client()


@pytest.mark.asyncio
async def test_closed_shared_client_is_recreated(client_factory):
    await _call()
    await aclose_shared_client()
    await _call()
    assert len(client_factory.created) == 2
    assert transport_mod._shared_clients[asyncio.get_running_loop()] is client_factory.created[1]
    await aclose_shared_client()


@pytest.mark.asyncio
async def test_aclose_is_idempotent(client_factory):
    await aclose_shared_client()
    await aclose_shared_client()
    assert client_factory.created == []


def test_shared_client_is_isolated_per_event_loop(monkeypatch):
    """顺序跑两个独立事件循环：各自建 1 个 client，不复用跨 loop 连接池。"""
    created: list[httpx.AsyncClient] = []
    real_cls = httpx.AsyncClient

    def factory(*args, **kwargs):
        client = real_cls(transport=httpx.MockTransport(_handler))
        created.append(client)
        return client

    monkeypatch.setattr(transport_mod.httpx, "AsyncClient", factory)

    async def one_loop() -> None:
        assert await _call() == {"ok": True}
        await aclose_shared_client()

    asyncio.run(one_loop())
    asyncio.run(one_loop())
    assert len(created) == 2

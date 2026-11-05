"""T50 / 5.1：qc-service DpClient lifespan 级单例 单测。

验收判据：
- 依赖注入返回同一实例（MUST NOT 每请求新建——fd 泄漏源）；
- reset_dp_client() 后可注入新实例（测试复位语义）；
- aclose_dp_client() 在 shutdown 路径被调用恰好一次且幂等（重复调用不报错）；
- aclose 异常 warning 不中断关停（清单内单点失败隔离）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app import deps as deps_mod
from app.deps import (
    aclose_dp_client,
    get_dp_client,
    reset_dp_client,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_dp_client()
    yield
    reset_dp_client()


class TestDpClientSingleton:
    def test_get_dp_client_returns_same_instance(self):
        """连续获取返回同一实例（lifespan 级持有，不每请求新建）。"""
        c1 = get_dp_client()
        c2 = get_dp_client()
        assert c1 is c2

    def test_reset_dp_client_allows_new_instance(self):
        """reset 后返回新实例（测试可注入替身）。"""
        c1 = get_dp_client()
        reset_dp_client()
        c2 = get_dp_client()
        assert c1 is not c2

    @pytest.mark.asyncio
    async def test_aclose_dp_client_calls_aclose_once_and_idempotent(self):
        """shutdown 清单：aclose 被调用恰好一次；重复调用幂等（无实例即 no-op）。"""
        client = get_dp_client()
        client.aclose = AsyncMock()

        await aclose_dp_client()
        client.aclose.assert_awaited_once()

        # 幂等：实例已置 None，重复调用不报错、不再触发 aclose
        await aclose_dp_client()
        client.aclose.assert_awaited_once()
        assert deps_mod._dp_client is None

    @pytest.mark.asyncio
    async def test_aclose_failure_does_not_break_shutdown(self, monkeypatch):
        """aclose 异常：warning 留痕不中断关停（清单单点失败隔离）。"""
        spy = Mock()
        monkeypatch.setattr(deps_mod, "logger", spy)
        client = get_dp_client()
        client.aclose = AsyncMock(side_effect=RuntimeError("conn broken"))

        await aclose_dp_client()  # 不抛

        client.aclose.assert_awaited_once()
        spy.warning.assert_called_once()
        assert spy.warning.call_args.args[0] == "qc_dp_client_aclose_failed"
        assert deps_mod._dp_client is None, "失败同样置 None（下次调用重建新实例）"


class TestBackgroundSubscriberUsesSingleton:
    def test_provide_dp_client_returns_lifespan_singleton(self):
        """W4：后台订阅的 DpClient 改走 lifespan 级单例——`_provide_dp_client`
        返回与 deps 单例同一实例，不再自建"只建不释"的独立客户端。"""
        from app.events.subscriber import _provide_dp_client

        singleton = get_dp_client()
        assert _provide_dp_client() is singleton

    @pytest.mark.asyncio
    async def test_singleton_is_closed_by_shutdown_checklist(self):
        """W4 闭环：订阅持有的单例由 shutdown 清单 aclose_dp_client 释放。"""
        from app.events.subscriber import _provide_dp_client

        client = _provide_dp_client()
        client.aclose = AsyncMock()

        await aclose_dp_client()
        client.aclose.assert_awaited_once()
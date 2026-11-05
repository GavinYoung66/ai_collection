"""T57 / 5.2：共享 LLM client 关停接入 shutdown 清单 单测。

验收判据：
- ``asyncio.run`` 两次调用场景：第二次 ``aclose_shared_client`` 将第一个
  loop 的残留 client 防御性关闭（消除旧事件循环 client 悬挂至进程退出）；
- 当前 loop 的 client 恰好被关闭一次（幂等：重复调用 no-op）；
- 无运行事件循环时安全返回（不抛）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio

from common.llm.transport import _get_shared_client, _shared_clients, aclose_shared_client


class TestSharedClientShutdown:
    def test_stale_loop_client_closed_on_second_run(self):
        """两次 asyncio.run：第二次 aclose 把第一个 loop 的残留 client 关闭。"""
        clients: list = []
        loops: list = []

        async def just_call():
            loops.append(asyncio.get_running_loop())  # 保 loop 强引用防 GC 清条目
            clients.append(_get_shared_client())

        async def call_and_close():
            loops.append(asyncio.get_running_loop())
            clients.append(_get_shared_client())
            await aclose_shared_client()

        asyncio.run(just_call())
        assert not clients[0].is_closed, "第一个 loop 关闭后 client 仍悬挂（缺陷场景）"

        asyncio.run(call_and_close())
        assert clients[0].is_closed, "旧 loop 残留 client 应被防御性关闭（T57）"
        assert clients[1].is_closed, "当前 loop 的 client 应被关闭"

    def test_current_loop_client_closed_exactly_once(self):
        """当前 loop：aclose 恰好一次；重复调用幂等（字典已 pop，no-op）。"""
        clients: list = []

        async def call_and_close_twice():
            clients.append(_get_shared_client())
            await aclose_shared_client()
            await aclose_shared_client()  # 幂等
            return asyncio.get_running_loop()

        loop = asyncio.run(call_and_close_twice())
        assert clients[0].is_closed
        assert _shared_clients.get(loop) is None, "aclose 后条目应移除"

    def test_no_running_loop_safe_return(self):
        """无运行事件循环：安全返回不抛（shutdown 路径健壮性）。"""
        # 同步上下文直接调用（asyncio.run 已退出）
        asyncio.run(aclose_shared_client())  # 不抛即通过

    def test_close_failure_does_not_propagate(self, monkeypatch):
        """client.aclose 异常：warning 留痕不中断关停（best-effort）。"""
        from unittest.mock import Mock

        spy = Mock()
        monkeypatch.setattr("common.llm.transport.logger", spy)

        class _BrokenClient:
            def __init__(self):
                self.is_closed = False
                self.aclose_called = 0

            async def aclose(self):
                self.aclose_called += 1
                raise RuntimeError("conn gone")

        broken = _BrokenClient()

        async def scenario():
            loop = asyncio.get_running_loop()
            _shared_clients[loop] = broken  # type: ignore[assignment]
            await aclose_shared_client()

        asyncio.run(scenario())
        assert broken.aclose_called == 1
        assert spy.warning.called, "关闭失败必须 warning 留痕"
        assert not _shared_clients, "失败条目同样应被 pop（不残留悬挂引用）"
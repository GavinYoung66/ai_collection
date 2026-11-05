"""T59 / 5.4：mcp-gateway bridge 连接池关闭出口 单测。

验收判据：
- aclose_toolsets 对登记的每个 toolset 的 aclose 恰好调用一次，完毕清空登记；
- 单点失败 warning 不中断其余关闭（清单骨架形态）；
- qc_mcp_client._dp_client 关闭幂等（pop + aclose + 置 None，重复调用 no-op）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app import qc_mcp_client
from app.tools import _TOOLSETS, aclose_toolsets



@pytest.fixture(autouse=True)
def _restore_toolsets():
    """测试后恢复登记表，防跨用例污染。"""
    snapshot = list(_TOOLSETS)
    yield
    _TOOLSETS[:] = snapshot


class TestAcloseToolsets:
    def test_build_tool_routes_registration_is_idempotent(self):
        """S2：build_tool_routes 二次调用不产生重复登记（幂等保护）。

        旧实现 `_TOOLSETS.extend` 无幂等——二次装配会让同一 shutdown 清单
        出现 8 项重复登记、关闭被调用两次；现先清空再登记。显式重建登记，
        不依赖模块加载态（其他用例的 lifespan shutdown 会清空登记表）。
        """
        from app.tools import build_tool_routes

        build_tool_routes()
        assert len(_TOOLSETS) == 4, f"装配后应登记 4 个 toolset，实际 {len(_TOOLSETS)}"

        build_tool_routes()
        build_tool_routes()

        assert len(_TOOLSETS) == 4, "二次装配不得追加登记（防 shutdown 重复关闭）"

    @pytest.mark.asyncio
    async def test_closes_each_registered_toolset_once(self):
        """装配的 4 个 toolset：aclose 各恰好一次，完毕登记表清空。

        显式调用 build_tool_routes 重建登记——不依赖模块加载态（其他用例的
        lifespan shutdown 会清空登记表，隐式依赖会随用例顺序翻转）。
        """
        from app.tools import build_tool_routes

        build_tool_routes()
        assert len(_TOOLSETS) == 4, f"装配应登记 4 个 toolset，实际 {len(_TOOLSETS)}"
        registered = list(_TOOLSETS)
        for ts in registered:
            ts.aclose = AsyncMock()

        await aclose_toolsets()

        for ts in registered:
            ts.aclose.assert_awaited_once()
        assert _TOOLSETS == [], "关闭后登记表应清空"

    @pytest.mark.asyncio
    async def test_closes_each_registered_toolset_once_ref(self):
        """（引用形态）关闭前捕获引用：每个 aclose 恰好一次。"""
        from app.tools import build_tool_routes

        build_tool_routes()
        registered = list(_TOOLSETS)
        for ts in registered:
            ts.aclose = AsyncMock()

        await aclose_toolsets()

        for ts in registered:
            ts.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_failure_does_not_break_others(self, monkeypatch):
        """单点失败：warning 留痕，其余 toolset 仍被关闭。"""
        spy = Mock()
        monkeypatch.setattr("app.tools.logger", spy)

        broken = Mock()
        broken.aclose = AsyncMock(side_effect=RuntimeError("pool gone"))
        ok1 = Mock()
        ok1.aclose = AsyncMock()
        ok2 = Mock()
        ok2.aclose = AsyncMock()
        _TOOLSETS[:] = [broken, ok1, ok2]

        await aclose_toolsets()  # 不抛

        broken.aclose.assert_awaited_once()
        ok1.aclose.assert_awaited_once()
        ok2.aclose.assert_awaited_once()
        spy.warning.assert_called_once()
        assert spy.warning.call_args.kwargs.get("toolset") == "Mock"
        assert _TOOLSETS == []


class TestAcloseDpClient:
    @pytest.mark.asyncio
    async def test_aclose_dp_client_idempotent(self, monkeypatch):
        """共享 DP 快照客户端：aclose 恰一次 + 置 None；重复调用 no-op。"""
        fake = Mock()
        fake.aclose = AsyncMock()
        monkeypatch.setattr(qc_mcp_client, "_dp_client", fake)

        await qc_mcp_client.aclose_dp_client()
        fake.aclose.assert_awaited_once()
        assert qc_mcp_client._dp_client is None

        await qc_mcp_client.aclose_dp_client()  # 幂等：None 即 no-op
        fake.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aclose_dp_client_failure_isolated(self, monkeypatch):
        """aclose 失败：warning 留痕不抛，条目仍置 None。"""
        spy = Mock()
        monkeypatch.setattr(qc_mcp_client, "logger", spy)
        fake = Mock()
        fake.aclose = AsyncMock(side_effect=RuntimeError("conn gone"))
        monkeypatch.setattr(qc_mcp_client, "_dp_client", fake)

        await qc_mcp_client.aclose_dp_client()  # 不抛

        fake.aclose.assert_awaited_once()
        spy.warning.assert_called_once()
        assert spy.warning.call_args.args[0] == "qc_mcp_dp_client_aclose_failed"
        assert qc_mcp_client._dp_client is None
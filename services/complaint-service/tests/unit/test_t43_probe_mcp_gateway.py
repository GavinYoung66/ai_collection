"""T43：`_probe_mcp_gateway` 异步化（httpx.AsyncClient），不再阻塞事件循环。

覆盖风险：原实现在异步 lifespan 中调用阻塞 `urllib.request.urlopen`
（最长 2×2s），冻结事件循环、劣化并发健康检查响应。
验收判据：并发健康检查响应时间不因探测劣化（事件循环在探测期间持续调度）。
"""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from app.config import settings
from app.main import _probe_mcp_gateway

GW = "http://gw.test:9000"


@pytest.fixture()
def standalone_real(monkeypatch):
    """standalone + 非 Mock + 已配置网关地址：进入真实探测分支。"""
    monkeypatch.setattr(settings, "MCP_GATEWAY_MODE", "standalone")
    monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
    monkeypatch.setattr(settings, "MCP_GATEWAY_URL", GW)
    return settings


class TestProbeShortCircuit:
    """短路分支：不发任何 HTTP 请求。"""

    async def test_inline_mode_returns_true_without_request(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "MCP_GATEWAY_MODE", "inline")
        assert await _probe_mcp_gateway() is True
        assert len(respx_mock.calls) == 0

    async def test_mock_mode_returns_true_without_request(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "MCP_GATEWAY_MODE", "standalone")
        monkeypatch.setattr(settings, "USE_MOCK_MCP", True)
        assert await _probe_mcp_gateway() is True
        assert len(respx_mock.calls) == 0

    async def test_empty_url_returns_false_without_request(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "MCP_GATEWAY_MODE", "standalone")
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "MCP_GATEWAY_URL", "")
        assert await _probe_mcp_gateway() is False
        assert len(respx_mock.calls) == 0


class TestProbeHappyAndFallback:
    """探测主路径与回退语义（与原 urllib 实现一致）。"""

    async def test_health_200_returns_true_after_single_request(
        self, standalone_real, respx_mock
    ):
        respx_mock.get(f"{GW}/health").mock(return_value=httpx.Response(200))
        assert await _probe_mcp_gateway() is True
        # /health 命中即返回，不再探测根路径
        assert len(respx_mock.calls) == 1

    async def test_health_404_falls_back_to_root_200(self, standalone_real, respx_mock):
        respx_mock.get(f"{GW}/health").mock(return_value=httpx.Response(404))
        respx_mock.get(f"{GW}/").mock(return_value=httpx.Response(200))
        assert await _probe_mcp_gateway() is True
        assert len(respx_mock.calls) == 2

    async def test_trailing_slash_url_normalized(self, monkeypatch, respx_mock):
        monkeypatch.setattr(settings, "MCP_GATEWAY_MODE", "standalone")
        monkeypatch.setattr(settings, "USE_MOCK_MCP", False)
        monkeypatch.setattr(settings, "MCP_GATEWAY_URL", GW + "/")
        respx_mock.get(f"{GW}/health").mock(return_value=httpx.Response(204))
        assert await _probe_mcp_gateway() is True

    async def test_5xx_on_both_paths_returns_false(self, standalone_real, respx_mock):
        respx_mock.get(f"{GW}/health").mock(return_value=httpx.Response(500))
        respx_mock.get(f"{GW}/").mock(return_value=httpx.Response(503))
        assert await _probe_mcp_gateway() is False


class TestProbeFailureModes:
    """异常场景：网络错误/超时均按不可达处理，不抛出。"""

    async def test_connect_error_returns_false(self, standalone_real, respx_mock):
        respx_mock.get(f"{GW}/health").mock(side_effect=httpx.ConnectError("refused"))
        respx_mock.get(f"{GW}/").mock(side_effect=httpx.ConnectError("refused"))
        assert await _probe_mcp_gateway() is False

    async def test_timeout_returns_false(self, standalone_real, respx_mock):
        respx_mock.get(f"{GW}/health").mock(side_effect=httpx.ConnectTimeout("t"))
        respx_mock.get(f"{GW}/").mock(side_effect=httpx.ReadTimeout("t"))
        assert await _probe_mcp_gateway() is False


class TestNoEventLoopBlocking:
    """验收判据：探测等待期间事件循环持续调度（并发健康检查不劣化）。"""

    async def test_slow_probe_keeps_loop_scheduling(self, standalone_real, respx_mock):
        async def slow_200(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(0.3)
            return httpx.Response(200)

        respx_mock.get(f"{GW}/health").mock(side_effect=slow_200)

        ticks = 0

        async def ticker():
            nonlocal ticks
            while True:
                await asyncio.sleep(0.01)
                ticks += 1

        task = asyncio.create_task(ticker())
        try:
            start = time.perf_counter()
            ok = await _probe_mcp_gateway()
            elapsed = time.perf_counter() - start
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        assert ok is True
        assert elapsed >= 0.25  # 确实等待了慢响应
        # 0.3s 内 10ms 心跳应约 30 次；阻塞式 urllib 下 ticks 会停在探测结束后
        assert ticks >= 15, f"事件循环被探测阻塞：{elapsed:.3f}s 内仅调度 {ticks} 次"

    def test_main_source_no_blocking_urllib_probe(self):
        """守护：main.py 不再出现阻塞 urllib 调用，lifespan 以 await 接入（防回潮）。"""
        import inspect

        src = inspect.getsource(_probe_mcp_gateway)
        assert "httpx.AsyncClient" in src
        assert asyncio.iscoroutinefunction(_probe_mcp_gateway)

        import app.main as main_module

        module_src = inspect.getsource(main_module)
        # docstring 允许提及历史缺陷，真实调用/导入不允许回潮
        assert "urlopen(" not in module_src
        assert "import urllib" not in module_src
        assert "await _probe_mcp_gateway()" in module_src

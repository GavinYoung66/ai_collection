"""T108：MCP 可靠性分级——retryable=False 异常不重试、不计熔断失败（spec mcp-gateway：工具参数边界收敛）。

下游因参数校验返回的 4xx MUST 视为不可重试，MUST NOT 计入熔断失败次数；
反复传非法参数不打开熔断，后续正常调用仍可执行。
"""
from __future__ import annotations

import pytest

from common.mcp import reliability as mcp_rel
from common.resilience.breaker import BreakerState


class _ParamError(Exception):
    """模拟 bridge_http 标记的下游 4xx 形态（retryable=False）。"""

    retryable = False


class TestT108RetryableGrading:
    @pytest.mark.asyncio
    async def test_non_retryable_called_once_and_no_breaker_failure(self):
        """不可重试错：只调用一次（跳过重试），不计熔断失败。"""
        calls = {"n": 0}

        async def transport(server, tool, payload, timeout):
            calls["n"] += 1
            raise _ParamError("bridge http 422: validation failed")

        mcp_rel.set_mcp_transport(transport)
        with pytest.raises(_ParamError):
            await mcp_rel.call_tool(
                "rag_kb", "search", {}, breaker_key="csmp:mcp_degraded:t108-a"
            )
        assert calls["n"] == 1
        breaker = mcp_rel._mcp_breakers.get_or_create("csmp:mcp_degraded:t108-a")
        assert breaker.state == BreakerState.CLOSED.value

    @pytest.mark.asyncio
    async def test_retryable_error_retries_and_counts(self):
        """可重试错（无 retryable 属性 → 默认可重试）：重试 3 次计一次失败。"""
        calls = {"n": 0}

        async def transport(server, tool, payload, timeout):
            calls["n"] += 1
            raise RuntimeError("bridge http 503")

        mcp_rel.set_mcp_transport(transport)
        with pytest.raises(RuntimeError):
            await mcp_rel.call_tool(
                "rag_kb", "search", {}, breaker_key="csmp:mcp_degraded:t108-b"
            )
        assert calls["n"] == 3
        breaker = mcp_rel._mcp_breakers.get_or_create("csmp:mcp_degraded:t108-b")
        assert breaker.state == BreakerState.CLOSED.value  # 1 次失败（阈值 5）未 open

    @pytest.mark.asyncio
    async def test_repeated_param_errors_never_open_breaker(self):
        """spec 场景：反复传非法参数不打开熔断，后续正常调用仍可执行。"""
        calls = {"n": 0}

        async def transport(server, tool, payload, timeout):
            calls["n"] += 1
            raise _ParamError("bridge http 422")

        mcp_rel.set_mcp_transport(transport)
        key = "csmp:mcp_degraded:t108-storm"
        for _ in range(10):
            with pytest.raises(_ParamError):
                await mcp_rel.call_tool("rag_kb", "search", {}, breaker_key=key)
        assert calls["n"] == 10
        breaker = mcp_rel._mcp_breakers.get_or_create(key)
        assert breaker.state == BreakerState.CLOSED.value  # 熔断未打开
        # 后续正常调用仍可执行
        mcp_rel.set_mcp_transport(
            _ok_transport
        )
        result = await mcp_rel.call_tool("rag_kb", "search", {}, breaker_key=key)
        assert result == {"ok": True}


async def _ok_transport(server, tool, payload, timeout):
    return {"ok": True}
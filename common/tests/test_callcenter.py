"""common.callcenter 模块单元测试。"""
import pytest

from common.callcenter.provider import CCCProvider, CallResult, TransferResult


class TestCCCProvider:
    @pytest.mark.asyncio()
    async def test_mock_make_call(self):
        provider = CCCProvider()
        result = await provider.make_call("13812341234", "case-001", "session-001")
        assert isinstance(result, CallResult)
        assert len(result.call_id) > 0

    @pytest.mark.asyncio()
    async def test_mock_transfer(self):
        provider = CCCProvider()
        result = await provider.transfer_to_human("session-001", "agent-001")
        assert isinstance(result, TransferResult)
        assert result.session_id == "session-001"

    @pytest.mark.asyncio()
    async def test_mock_hangup(self):
        provider = CCCProvider()
        success = await provider.hangup("session-001")
        assert isinstance(success, bool)

    @pytest.mark.asyncio()
    async def test_concurrency_semaphore(self):
        provider = CCCProvider()
        assert provider._max_concurrency == 50
        assert hasattr(provider._semaphore, "_semaphore")

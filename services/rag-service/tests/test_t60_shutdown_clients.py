"""T60 / 5.5：rag-service shutdown 清单补齐三组 client 单测。

验收判据：
- DashScopeASRProvider.aclose 关闭内部 httpx client（幂等：重复调用不再触发）；
- ASRClient 惰性持有 provider（两次转写只 create 一次），aclose 关闭 provider
  并幂等；
- LLMService.aclose 转调 transport 共享 client 关闭（幂等）；
- rag shutdown()：三组新增清单项（_ocr_client/_asr_client/llm_shared_client）
  在关停路径各恰好调用一次（monkeypatch 计数，守护测试 11.2）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.services.asr_client import ASRClient
from app.services.asr_providers.dashscope_provider import DashScopeASRProvider
from app.services.llm_service import LLMService


class TestDashScopeProviderAclose:
    @pytest.mark.asyncio
    async def test_aclose_closes_client_idempotent(self):
        """aclose：client 关闭一次 + 置 None；重复调用 no-op（幂等）。"""
        provider = DashScopeASRProvider()
        fake = Mock()
        fake.is_closed = False
        fake.aclose = AsyncMock()
        provider._client = fake

        await provider.aclose()
        assert fake.aclose.await_count == 1
        assert provider._client is None

        await provider.aclose()  # 幂等：_client 为 None 即 no-op
        assert fake.aclose.await_count == 1

    @pytest.mark.asyncio
    async def test_aclose_allows_rebuild_via_get_client(self):
        """aclose 后 _get_client 重建新 client（惰性，服务可用性保持）。"""
        provider = DashScopeASRProvider()
        old = Mock()
        old.is_closed = False
        old.aclose = AsyncMock()
        provider._client = old
        await provider.aclose()

        rebuilt = await provider._get_client()
        assert rebuilt is not old


class TestASRClientProviderLifecycle:
    @pytest.mark.asyncio
    async def test_transcribe_reuses_provider_and_aclose(self, monkeypatch):
        """两次转写只 create 一次 provider；aclose 关闭一次且幂等。"""
        client = ASRClient()
        created: list = []

        class FakeProvider:
            def __init__(self):
                self.transcribe_calls = 0
                self.aclose_calls = 0

            async def transcribe(self, audio_bytes, fmt):
                self.transcribe_calls += 1
                return "x"

            async def aclose(self):
                self.aclose_calls += 1

        def fake_create():
            p = FakeProvider()
            created.append(p)
            return p

        monkeypatch.setattr(
            "app.services.asr_client.ASRFactory.create", fake_create
        )

        await client.transcribe(b"fake-wav-bytes", "fake.wav")
        await client.transcribe(b"fake-wav-bytes", "fake.wav")
        assert len(created) == 1, "provider 应惰性单例复用（不每转写新建）"
        assert created[0].transcribe_calls == 2

        await client.aclose()
        assert created[0].aclose_calls == 1
        assert client._provider is None

        await client.aclose()  # 幂等
        assert created[0].aclose_calls == 1


class TestLLMServiceAclose:
    @pytest.mark.asyncio
    async def test_aclose_delegates_to_shared_client(self, monkeypatch):
        """LLMService.aclose 转调 transport 共享 client 关闭（幂等可重复）。"""
        called: list[int] = []

        async def fake_aclose():
            called.append(1)

        monkeypatch.setattr(
            "common.llm.transport.aclose_shared_client", fake_aclose
        )
        svc = LLMService()

        await svc.aclose()
        await svc.aclose()
        assert len(called) == 2, "转调共享 client 关闭（transport 内部自身幂等）"

    @pytest.mark.asyncio
    async def test_aclose_closes_resolved_provider_clients(self, monkeypatch):
        """W4：_resolve_llm 解析出的 provider 自建 httpx client 一并关闭。"""
        async def fake_aclose():
            pass

        monkeypatch.setattr("common.llm.transport.aclose_shared_client", fake_aclose)

        class _Provider:
            def __init__(self):
                self.close = AsyncMock()

        provider = _Provider()
        svc = LLMService()
        svc._resolved = ("qwen-plus", [], provider)

        await svc.aclose()

        provider.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_aclose_provider_failure_does_not_break_shutdown(
        self, monkeypatch
    ):
        """W4：provider 关闭异常 warning 留痕，不阻断共享 client 关停。"""
        async def fake_aclose():
            pass

        monkeypatch.setattr("common.llm.transport.aclose_shared_client", fake_aclose)

        class _BrokenProvider:
            def __init__(self):
                self.close = AsyncMock(side_effect=RuntimeError("provider broken"))

        provider = _BrokenProvider()
        svc = LLMService()
        svc._resolved = ("qwen-plus", [], provider)

        await svc.aclose()  # 不抛

        provider.close.assert_awaited_once()


class TestRagShutdownThreeGroups:
    @pytest.mark.asyncio
    async def test_shutdown_closes_ocr_asr_llm_groups(self, monkeypatch):
        """关停路径：三组新增清单项各恰好调用一次（守护测试 11.2）。"""
        ocr = Mock()
        ocr.close = AsyncMock()
        asr = Mock()
        asr.aclose = AsyncMock()
        rewrite = Mock()
        rewrite.close = AsyncMock()
        monkeypatch.setattr("app.api.deps._ocr_client", ocr)
        monkeypatch.setattr("app.api.deps._asr_client", asr)
        monkeypatch.setattr("app.api.deps._rewrite_service", rewrite)

        shared_calls: list[int] = []

        async def fake_shared():
            shared_calls.append(1)

        monkeypatch.setattr(
            "common.llm.transport.aclose_shared_client", fake_shared
        )
        monkeypatch.setattr("app.main._unwire_resilience_hooks", lambda: None)

        import common.db.session as db_session

        class _FakeEngine:
            def __init__(self):
                self.dispose_calls = 0

            async def dispose(self):
                self.dispose_calls += 1

        monkeypatch.setattr(db_session, "engine", _FakeEngine())

        from app import main as rag_main

        await rag_main.shutdown()

        ocr.close.assert_awaited_once()
        asr.aclose.assert_awaited_once()
        rewrite.close.assert_awaited_once()
        assert len(shared_calls) == 1
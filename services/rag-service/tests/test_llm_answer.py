"""LLM 回答服务测试（V5 + rag-resilience-wiring 2.x）。

覆盖：开关门控 / 脱敏与红线 / invoke_llm fallback / 分片熔断键 /
LLMRateLimitError 降级 / factory 三条链 / LLM_TIMEOUT 默认 10。
"""
import asyncio
import json

import pytest

from app.models.search import SearchRequest
from app.services import query_cache
from app.services.llm_providers.base import (
    _load_prompt_template,
    build_answer_prompt,
)
from app.services.llm_providers.dashscope_provider import DashScopeProvider
from app.services.llm_providers.deepseek_provider import DeepSeekProvider
from app.services.llm_providers.factory import LLMFactory
from app.services.llm_providers.local_openai_provider import LocalOpenAIProvider
from app.services.llm_service import LLMService, mask_sensitive
from app.settings import settings
from common.config.settings import Settings
from common.llm import reliability as reliability_mod
from common.llm.reliability import set_default_service
from common.resilience._redis import configure_redis, reset_redis
from common.resilience.breaker import make_llm_breaker_key


class FakeRedis:
    """内存版 Redis：限流 SET NX + INCR，以及 degrade SETEX。"""

    def __init__(self, fail=False):
        self.store: dict = {}
        self.ttls: dict = {}
        self.fail = fail
        self.calls = {"exists": 0, "setex": 0, "set": 0, "incr": 0}

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("redis down")

    async def get(self, key):
        self._maybe_fail()
        return self.store.get(key)

    async def exists(self, key):
        self.calls["exists"] += 1
        self._maybe_fail()
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):
        self.calls["setex"] += 1
        self._maybe_fail()
        self.store[key] = value
        self.ttls[key] = ttl

    async def set(self, key, value, ex=None, nx=False):
        self.calls["set"] += 1
        self._maybe_fail()
        if nx and key in self.store:
            return None
        self.store[key] = int(value) if str(value).isdigit() else value
        if ex is not None:
            self.ttls[key] = ex
        return True

    async def incr(self, key):
        self.calls["incr"] += 1
        self._maybe_fail()
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self._maybe_fail()
        self.ttls[key] = ttl

    async def delete(self, *keys):
        for key in keys:
            self.store.pop(key, None)
            self.ttls.pop(key, None)


class FakeTransport:
    """invoke_llm transport mock：(model, messages, timeout_seconds) -> str。"""

    def __init__(self, result=None, error=None, by_model=None, tracker=None):
        self.result = result
        self.error = error
        self.by_model = by_model or {}
        self.calls: list = []
        self._tracker = tracker

    async def __call__(self, model, messages, timeout_seconds):
        self.calls.append((model, messages, timeout_seconds))
        if self._tracker is not None:
            self._tracker["active"] += 1
            self._tracker["max"] = max(self._tracker["max"], self._tracker["active"])
            await asyncio.sleep(0.02)
            self._tracker["active"] -= 1
        if model in self.by_model:
            spec = self.by_model[model]
            if isinstance(spec, BaseException):
                raise spec
            if callable(spec) and not isinstance(spec, str):
                return await spec(model, messages, timeout_seconds)
            return spec
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(settings, "KB_LLM_ANSWER_ENABLED", True)


@pytest.fixture
def llm_isolate(monkeypatch):
    fake = FakeRedis()
    configure_redis(fake)
    set_default_service("rag")
    reliability_mod._llm_breakers.clear()
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    yield fake
    reliability_mod._llm_breakers.clear()
    set_default_service(None)
    reset_redis()


def _svc(transport, **kwargs):
    kwargs.setdefault("model", "qwen-plus")
    kwargs.setdefault("fallback_chain", [])
    return LLMService(transport=transport, **kwargs)


class TestMaskSensitive:
    def test_idcard_phone_amount_masked(self):
        """脱敏顺序：身份证→手机号→金额（身份证整体替换，不被手机号正则截断）。"""
        text = "客户身份证 110101199001011234，电话 13812345678，金额 5000 元"
        masked = mask_sensitive(text)
        assert "[身份证号]" in masked
        assert "[手机号]" in masked
        assert "[金额]" in masked
        assert "110101" not in masked
        assert "1234" not in masked

    def test_amount_commas_and_long_numbers(self):
        assert "[金额]" in mask_sensitive("费用 12,500 元整")
        assert "[金额]" in mask_sensitive("上限 50000元")

    def test_no_sensitive_unchanged(self):
        text = "普通制度文本，无敏感信息"
        assert mask_sensitive(text) == text


class TestFactoryChains:
    def test_dashscope_plus_fallback_turbo(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PROVIDER", "dashscope")
        monkeypatch.setattr(settings, "LLM_MODEL", "qwen-plus")
        model, chain, transport = LLMFactory.create()
        assert model == "qwen-plus"
        assert chain == ["qwen-turbo"]
        assert isinstance(transport, DashScopeProvider)

    def test_dashscope_turbo_no_fallback(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_MODEL", "qwen-turbo")
        model, chain, transport = LLMFactory.create("dashscope")
        assert model == "qwen-turbo"
        assert chain == []
        assert isinstance(transport, DashScopeProvider)

    def test_deepseek_single_chain(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PROVIDER", "deepseek")
        model, chain, transport = LLMFactory.create()
        assert model == "deepseek-chat"
        assert chain == []
        assert isinstance(transport, DeepSeekProvider)

    def test_local_uses_llm_model(self, monkeypatch):
        monkeypatch.setattr(settings, "LLM_PROVIDER", "local")
        monkeypatch.setattr(settings, "LLM_MODEL", "llama3")
        model, chain, transport = LLMFactory.create()
        assert model == "llama3"
        assert chain == []
        assert isinstance(transport, LocalOpenAIProvider)

    @pytest.mark.asyncio
    async def test_dashscope_transport_uses_native_endpoint(self, monkeypatch):
        captured = {}

        async def fake_post_json(**kwargs):
            captured.update(kwargs)
            return {"output": {"text": "ok"}}

        monkeypatch.setattr(
            "app.services.llm_providers.dashscope_provider.post_json", fake_post_json
        )
        monkeypatch.setattr(settings, "DASHSCOPE_API_KEY", "sk-real")
        text = await DashScopeProvider()(
            "qwen-plus", [{"role": "user", "content": "hi"}], 10
        )
        assert text == "ok"
        assert captured["url"] == settings.LLM_DASHSCOPE_ENDPOINT
        assert "compatible-mode" not in captured["url"]

    def test_llm_timeout_default_is_10(self):
        assert Settings.model_fields["LLM_TIMEOUT"].default == 10


class TestGate:
    @pytest.mark.asyncio
    async def test_switch_off_zero_call_v4(self, monkeypatch, llm_isolate):
        monkeypatch.setattr(settings, "KB_LLM_ANSWER_ENABLED", False)
        transport = FakeTransport(result="不该被调用")
        svc = _svc(transport)
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["answer"] is None
        assert r["degraded"] is False
        assert r["llm_called"] is False
        assert r["llm_skip_reason"] == "KB_LLM_ANSWER_ENABLED=false"
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_answer_true_generates(self, switch_on, llm_isolate):
        transport = FakeTransport(result="按制度可协商分期")
        svc = _svc(transport)
        r = await svc.generate_answer(
            "能否分期", [{"content": "逾期90天可申请协商", "doc_id": "d1"}], True
        )
        assert r["answer"] == "按制度可协商分期"
        assert r["citations"] == ["d1"]
        assert r["llm_called"] is True and r["degraded"] is False
        assert len(transport.calls) == 1
        user = transport.calls[0][1][1]["content"]
        assert "逾期90天可申请协商" in user
        assert transport.calls[0][1][1]["role"] == "user"

    @pytest.mark.asyncio
    async def test_answer_not_requested_no_call(self, switch_on, llm_isolate):
        transport = FakeTransport(result="x")
        svc = _svc(transport)
        r = await svc.generate_answer("问题", [{"content": "正文"}], False)
        assert r["llm_called"] is False
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_empty_context_no_call(self, switch_on, llm_isolate):
        transport = FakeTransport(result="x")
        svc = _svc(transport)
        r = await svc.generate_answer("问题", [], True)
        assert r["answer"] is None
        assert r["degraded"] is False
        assert r["llm_called"] is False
        assert r["llm_skip_reason"] == "empty_context"
        assert transport.calls == []


class TestDegrade:
    @pytest.mark.asyncio
    async def test_primary_timeout_fallback_success(self, switch_on, llm_isolate):
        transport = FakeTransport(
            by_model={
                "qwen-plus": asyncio.TimeoutError("slow"),
                "qwen-turbo": "备用回答",
            }
        )
        svc = _svc(transport, fallback_chain=["qwen-turbo"])
        r = await svc.generate_answer("问题", [{"content": "正文", "doc_id": "d1"}], True)
        assert r["answer"] == "备用回答"
        assert r["degraded"] is False
        assert r["llm_called"] is True
        assert [c[0] for c in transport.calls] == ["qwen-plus", "qwen-turbo"]

    @pytest.mark.asyncio
    async def test_chain_fail_empty_answer(self, switch_on, llm_isolate):
        transport = FakeTransport(error=RuntimeError("llm down"))
        svc = _svc(transport, fallback_chain=["qwen-turbo"])
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_called"] is True
        assert r["citations"] == []

    @pytest.mark.asyncio
    async def test_writes_model_key_not_global(self, switch_on, llm_isolate):
        transport = FakeTransport(error=RuntimeError("llm down"))
        svc = _svc(transport)
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["degraded"] is True
        assert llm_isolate.store.get("rag:llm_degraded:qwen-plus") == "1"
        assert llm_isolate.ttls.get("rag:llm_degraded:qwen-plus") == 60
        assert "rag:llm:degraded" not in llm_isolate.store

    @pytest.mark.asyncio
    async def test_primary_open_does_not_block_backup(self, switch_on, llm_isolate):
        breaker = reliability_mod._llm_breakers.get_or_create(
            make_llm_breaker_key("rag", "qwen-plus")
        )
        for _ in range(breaker.fail_max):
            await breaker.record_failure()
        transport = FakeTransport(
            by_model={
                "qwen-plus": RuntimeError("should not call"),
                "qwen-turbo": "备用仍可用",
            }
        )
        svc = _svc(transport, fallback_chain=["qwen-turbo"])
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["answer"] == "备用仍可用"
        assert [c[0] for c in transport.calls] == ["qwen-turbo"]

    @pytest.mark.asyncio
    async def test_rate_limit_error_degrades(self, switch_on, llm_isolate, monkeypatch):
        async def _deny(*_a, **_k):
            return False

        monkeypatch.setattr(reliability_mod, "rate_limit_allow", _deny)
        transport = FakeTransport(result="不该被调用")
        svc = _svc(transport)
        r = await svc.generate_answer("问题", [{"content": "正文", "doc_id": "d1"}], True)
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_skip_reason"] == "llm_rate_limited"
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_chain_fail_search_still_returns_hits(self, switch_on, llm_isolate):
        """整链失败：空 answer + degraded，检索 results（BM25/关键词命中）仍出。"""
        import uuid
        from unittest.mock import AsyncMock, MagicMock

        from app.services.search_service import SearchService

        doc_id = str(uuid.uuid4())
        chunk_id = str(uuid.uuid4())

        class MockLogService:
            async def log(self, **kwargs):
                pass

        class MockDocRepo:
            def __init__(self):
                self.db = MagicMock()
                empty = MagicMock()
                empty.scalars.return_value.all.return_value = []
                self.db.execute = AsyncMock(return_value=empty)

            async def get_titles_map(self, uid_list):
                return {doc_id: "催收合规制度"}

            async def get_meta_dict(self, uid_list):
                return {doc_id: {"version": 2, "source": "v1_legacy"}}

        class MockMilvus:
            async def search(self, query_vector, top_n=20, category=None):
                return [{
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "category": "regulation",
                    "score": 0.9,
                }]

        class MockES:
            async def search(self, query, top_n=20, category=None):
                return [{
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "category": "regulation",
                    "content": "逾期协商关键词命中",
                    "score": 5.0,
                }]

        class MockEmbed:
            async def embed(self, text):
                return [0.1] * 1024

        class MockRerank:
            async def is_degraded(self):
                return False

            async def rerank(self, query, candidates):
                return [
                    {"chunk_id": c["chunk_id"], "rerank_score": 0.9}
                    for c in candidates
                ]

        transport = FakeTransport(error=RuntimeError("llm down"))
        search_svc = SearchService(
            milvus_client=MockMilvus(),
            es_client=MockES(),
            embed_client=MockEmbed(),
            rerank_client=MockRerank(),
            log_service=MockLogService(),
            doc_repo=MockDocRepo(),
            llm_service=_svc(transport, fallback_chain=["qwen-turbo"]),
        )
        result = await search_svc.hybrid_search(
            query="逾期协商", category="regulation", top_k=5, answer=True
        )
        assert result["answer"] is None
        assert result["degraded"] is True
        assert result["citations"] == []
        assert result["results"]
        assert any("逾期协商" in (hit.get("content") or "") for hit in result["results"])

    @pytest.mark.asyncio
    async def test_max_concurrency_limits_parallel_calls(self, switch_on, llm_isolate):
        tracker = {"active": 0, "max": 0}
        transport = FakeTransport(result="ok", tracker=tracker)
        svc = _svc(transport, max_concurrency=2)
        rs = await asyncio.gather(*[
            svc.generate_answer("问题", [{"content": "正文"}], True)
            for _ in range(3)
        ])
        assert all(r["answer"] == "ok" for r in rs)
        assert tracker["max"] <= 2
        assert len(transport.calls) == 3


class _WordsProvider:
    async def __call__(self):
        return {"暴力催收", "威胁上门"}


class _FullWordsProvider:
    async def __call__(self):
        return {"暴力催收", "威胁上门", "老赖", "起诉坐牢"}


class TestRedline:
    @pytest.mark.asyncio
    async def test_pre_redline_block_zero_llm_call(self, switch_on, llm_isolate):
        transport = FakeTransport(result="x")
        svc = _svc(transport, word_provider=_WordsProvider())
        r = await svc.generate_answer(
            "问题",
            [{"content": "建议暴力催收、威胁上门", "doc_id": "d1", "category": "case"}],
            True,
        )
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_called"] is False
        assert r["llm_skip_reason"] == "pre_redline_blocked"
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_pre_redline_skip_other_compliance_manual(self, switch_on, llm_isolate):
        transport = FakeTransport(result="流程说明")
        svc = _svc(transport, word_provider=_WordsProvider())
        r = await svc.generate_answer(
            "话术风控流程",
            [{"content": "禁止老赖、起诉坐牢等恐吓话术", "doc_id": "d1", "category": "other"}],
            True,
        )
        assert r["llm_called"] is True
        assert r["answer"] == "流程说明"

    @pytest.mark.asyncio
    async def test_pre_redline_block_asr_source(self, switch_on, llm_isolate):
        transport = FakeTransport(result="x")
        svc = _svc(transport, word_provider=_FullWordsProvider())
        r = await svc.generate_answer(
            "问题",
            [{"content": "你是老赖必须起诉坐牢", "doc_id": "d1", "category": "regulation", "source": "asr"}],
            True,
        )
        assert r["llm_skip_reason"] == "pre_redline_blocked"
        assert transport.calls == []

    @pytest.mark.asyncio
    async def test_pre_redline_skip_regulation_context(self, switch_on, llm_isolate):
        transport = FakeTransport(result="请遵循文明催收规范，不得威胁客户")
        svc = _svc(transport, word_provider=_WordsProvider())
        r = await svc.generate_answer(
            "问题",
            [{"content": "催收过程不得暴力催收、威胁上门", "doc_id": "d1", "category": "regulation"}],
            True,
        )
        assert r["answer"] == "请遵循文明催收规范，不得威胁客户"
        assert r["llm_called"] is True
        assert len(transport.calls) == 1

    @pytest.mark.asyncio
    async def test_post_redline_discard_answer(self, switch_on, llm_isolate):
        transport = FakeTransport(result="建议催收员暴力催收施压")
        svc = _svc(transport, word_provider=_WordsProvider())
        r = await svc.generate_answer("问题", [{"content": "制度正文"}], True)
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_called"] is True
        assert r["llm_skip_reason"] == "post_redline_blocked"
        assert len(transport.calls) == 1

    @pytest.mark.asyncio
    async def test_sensitive_masked_before_injection(self, switch_on, llm_isolate):
        transport = FakeTransport(result="ok")
        svc = _svc(transport)
        ctx = [{
            "content": "客户电话 13812345678，身份证 110101199001011234，欠款 5000 元",
            "doc_id": "d1",
        }]
        await svc.generate_answer("问题", ctx, True)
        user = transport.calls[0][1][1]["content"]
        assert "13812345678" not in user
        assert "110101199001011234" not in user
        assert "[手机号]" in user
        assert "[身份证号]" in user
        assert "[金额]" in user


class TestPromptTemplate:
    def test_prompt_loaded_from_yaml_params_injected(self):
        data = _load_prompt_template()
        user_tpl = data.get("user", "")
        assert "{query}" in user_tpl and "{context}" in user_tpl
        system, user = build_answer_prompt(
            [{"content": "逾期90天可申请协商", "page": 2}], "能否分期"
        )
        assert system == data["system"]
        assert "能否分期" in user
        assert "[2页] 逾期90天可申请协商" in user
        assert "{query}" not in user and "{context}" not in user


class TestAnswerSkipsCache:
    @pytest.mark.asyncio
    async def test_answer_true_skips_cache_read_and_write(self, monkeypatch):
        from tests.test_query_cache import MockEmbed, _route_args
        from app.api import search as search_api

        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", True)
        fake = FakeRedis()
        monkeypatch.setattr(query_cache, "_redis", fake)

        req = SearchRequest(query="逾期协商", category="regulation", top_k=5, answer=True)
        key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        fake.store[key] = json.dumps(
            {"code": "0", "data": {"results": []}, "trace_id": "cached"},
            ensure_ascii=False,
        )

        embed = MockEmbed()
        resp = await search_api.hybrid_search(req, **_route_args(embed))
        assert resp["code"] == "0"
        assert embed.calls == 1
        assert fake.calls["exists"] == 0
        assert fake.calls["setex"] == 0

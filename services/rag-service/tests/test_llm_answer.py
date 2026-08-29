"""LLM 回答服务测试（V5，design D8；tasks 11.6）。

覆盖：开关关闭零 LLM 调用（响应与 V4 一致）/ answer=true 生成带溯源回答 / 未传 answer 不调用 /
双闸门兜底不调用 / 超时·熔断降级 answer=null+degraded=true / rag:llm:degraded TTL 60s /
敏感信息脱敏后注入 LLM（手机号/身份证/金额 → 占位符）/ 前置·后置合规拦截（零调用/丢弃） /
Prompt 从 conf/llm_answer_prompt.yaml 加载且业务变量参数注入（无硬编码） /
LLM_MAX_CONCURRENCY 并发上限 / 缓存命中但 answer=true 跳过缓存走真实检索。
"""
import asyncio
import json

import pytest

from app.models.search import SearchRequest
from app.services import query_cache
from app.services.llm_providers.base import (
    LLMAnswer,
    _load_prompt_template,
    build_answer_prompt,
)
from app.services.llm_service import LLMService, mask_sensitive
from common.config.settings import settings


class FakeRedis:
    """内存版 Redis mock（LLM 熔断标识探测/写入）。"""

    def __init__(self, fail=False):
        self.store: dict = {}
        self.ttls: dict = {}
        self.fail = fail
        self.calls = {"exists": 0, "setex": 0}

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("redis down")

    async def exists(self, key):
        self.calls["exists"] += 1
        self._maybe_fail()
        return 1 if key in self.store else 0

    async def setex(self, key, ttl, value):
        self.calls["setex"] += 1
        self._maybe_fail()
        self.store[key] = value
        self.ttls[key] = ttl


class FakeProvider:
    """可编排的 LLM Provider mock（记录注入上下文，可注入结果/异常/并发追踪）。"""

    def __init__(self, result=None, error=None, tracker=None):
        self.result = result
        self.error = error
        self.calls: list = []
        self._tracker = tracker

    async def generate(self, context, query, timeout):
        self.calls.append((context, query, timeout))
        if self._tracker is not None:
            self._tracker["active"] += 1
            self._tracker["max"] = max(self._tracker["max"], self._tracker["active"])
            await asyncio.sleep(0.02)
            self._tracker["active"] -= 1
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture
def switch_on(monkeypatch):
    monkeypatch.setattr(settings, "KB_LLM_ANSWER_ENABLED", True)


class TestMaskSensitive:
    def test_idcard_phone_amount_masked(self):
        """脱敏顺序：身份证→手机号→金额（身份证整体替换，不被手机号正则截断）。"""
        text = "客户身份证 110101199001011234，电话 13812345678，金额 5000 元"
        masked = mask_sensitive(text)
        assert "[身份证号]" in masked
        assert "[手机号]" in masked
        assert "[金额]" in masked
        # 先身份证后手机号：身份证中段/尾号不得被手机号正则截断而泄露
        assert "110101" not in masked
        assert "1234" not in masked

    def test_amount_commas_and_long_numbers(self):
        """金额：千分位与 5 位连续数字均匹配（防长数字串误伤）。"""
        assert "[金额]" in mask_sensitive("费用 12,500 元整")
        assert "[金额]" in mask_sensitive("上限 50000元")

    def test_no_sensitive_unchanged(self):
        text = "普通制度文本，无敏感信息"
        assert mask_sensitive(text) == text


class TestGate:
    @pytest.mark.asyncio
    async def test_switch_off_zero_call_v4(self, monkeypatch):
        """开关关闭：零 LLM 调用，响应与 V4 一致（answer=None, degraded=False）。"""
        monkeypatch.setattr(settings, "KB_LLM_ANSWER_ENABLED", False)
        provider = FakeProvider(result=LLMAnswer("不该被调用"))
        svc = LLMService(provider=provider)
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["answer"] is None
        assert r["degraded"] is False
        assert r["llm_called"] is False
        assert r["llm_skip_reason"] == "KB_LLM_ANSWER_ENABLED=false"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_answer_true_generates(self, switch_on):
        """开关开启 + answer=true：生成带溯源回答，上下文脱敏后参数注入。"""
        provider = FakeProvider(result=LLMAnswer("按制度可协商分期", ["d1"]))
        svc = LLMService(provider=provider)
        r = await svc.generate_answer(
            "能否分期", [{"content": "逾期90天可申请协商", "doc_id": "d1"}], True
        )
        assert r["answer"] == "按制度可协商分期"
        assert r["citations"] == ["d1"]  # 溯源引用（命中文档 doc_id）随结果返回
        assert r["llm_called"] is True and r["degraded"] is False
        assert len(provider.calls) == 1
        # 注入上下文为脱敏后参数（本例无敏感词，内容原样传递）
        assert provider.calls[0][0][0]["content"] == "逾期90天可申请协商"
        assert provider.calls[0][1] == "能否分期"

    @pytest.mark.asyncio
    async def test_answer_not_requested_no_call(self, switch_on):
        """开关开启但未传 answer：零 LLM 调用。"""
        provider = FakeProvider(result=LLMAnswer("x"))
        svc = LLMService(provider=provider)
        r = await svc.generate_answer("问题", [{"content": "正文"}], False)
        assert r["llm_called"] is False
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_empty_context_no_call(self, switch_on):
        """双闸门兜底（context 空）：零 LLM 调用。"""
        provider = FakeProvider(result=LLMAnswer("x"))
        svc = LLMService(provider=provider)
        r = await svc.generate_answer("问题", [], True)
        assert r["answer"] is None
        assert r["degraded"] is False
        assert r["llm_called"] is False
        assert r["llm_skip_reason"] == "empty_context"
        assert provider.calls == []


class TestDegrade:
    @pytest.mark.asyncio
    async def test_timeout_degraded(self, switch_on, monkeypatch):
        """LLM 超时：捕获返回 answer=null + degraded=true，不抛错。"""
        monkeypatch.setattr(settings, "LLM_TIMEOUT", 1)
        provider = FakeProvider(error=asyncio.TimeoutError("slow"))
        svc = LLMService(provider=provider)
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_called"] is True
        assert r["llm_skip_reason"] == "provider_error:TimeoutError"
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_circuit_breaker_after_3_failures(self, switch_on):
        """连续 3 次失败 → 熔断标识 rag:llm:degraded（TTL 60s），窗口内短路零调用。"""
        redis = FakeRedis()
        provider = FakeProvider(error=RuntimeError("llm down"))
        svc = LLMService(redis=redis, provider=provider)
        for _ in range(3):
            r = await svc.generate_answer("问题", [{"content": "正文"}], True)
            assert r["degraded"] is True
        assert redis.calls["setex"] == 1
        assert redis.ttls["rag:llm:degraded"] == 60
        # 熔断窗口内短路：不发起 LLM 调用（llm_called=False 区分实际调用）
        r = await svc.generate_answer("问题", [{"content": "正文"}], True)
        assert r["answer"] is None and r["degraded"] is True
        assert r["llm_called"] is False and r["citations"] == []
        assert len(provider.calls) == 3

    @pytest.mark.asyncio
    async def test_max_concurrency_limits_parallel_calls(self, switch_on):
        """LLM_MAX_CONCURRENCY 信号量：并发请求排队，LLM 调用不超限。"""
        tracker = {"active": 0, "max": 0}
        provider = FakeProvider(result=LLMAnswer("ok"), tracker=tracker)
        svc = LLMService(provider=provider, max_concurrency=2)
        rs = await asyncio.gather(*[
            svc.generate_answer("问题", [{"content": "正文"}], True)
            for _ in range(3)
        ])
        assert all(r["answer"] == "ok" for r in rs)
        assert tracker["max"] <= 2
        assert len(provider.calls) == 3


class _WordsProvider:
    async def __call__(self):
        return {"暴力催收", "威胁上门"}


class _FullWordsProvider:
    async def __call__(self):
        return {"暴力催收", "威胁上门", "老赖", "起诉坐牢"}


class TestRedline:
    @pytest.mark.asyncio
    async def test_pre_redline_block_zero_llm_call(self, switch_on):
        """前置合规拦截：非权威来源（case）chunk 命中红线 → 零 LLM 调用。"""
        provider = FakeProvider(result=LLMAnswer("x"))
        svc = LLMService(provider=provider, word_provider=_WordsProvider())
        r = await svc.generate_answer(
            "问题",
            [{"content": "建议暴力催收、威胁上门", "doc_id": "d1", "category": "case"}],
            True,
        )
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_called"] is False
        assert r["llm_skip_reason"] == "pre_redline_blocked"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_pre_redline_skip_other_compliance_manual(self, switch_on):
        """合规手册 other 类 chunk 含违禁词示例时不误拦（P2）。"""
        provider = FakeProvider(result=LLMAnswer("流程说明"))
        svc = LLMService(provider=provider, word_provider=_WordsProvider())
        r = await svc.generate_answer(
            "话术风控流程",
            [{"content": "禁止老赖、起诉坐牢等恐吓话术", "doc_id": "d1", "category": "other"}],
            True,
        )
        assert r["llm_called"] is True
        assert r["answer"] == "流程说明"

    @pytest.mark.asyncio
    async def test_pre_redline_block_asr_source(self, switch_on):
        """录音转写 asr 来源命中红线仍拦截。"""
        provider = FakeProvider(result=LLMAnswer("x"))
        svc = LLMService(provider=provider, word_provider=_FullWordsProvider())
        r = await svc.generate_answer(
            "问题",
            [{"content": "你是老赖必须起诉坐牢", "doc_id": "d1", "category": "regulation", "source": "asr"}],
            True,
        )
        assert r["llm_skip_reason"] == "pre_redline_blocked"
        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_pre_redline_skip_regulation_context(self, switch_on):
        """制度库引用违禁词示例时不应误拦（regulation/script 跳过前置拦截）。"""
        provider = FakeProvider(result=LLMAnswer("请遵循文明催收规范，不得威胁客户"))
        svc = LLMService(provider=provider, word_provider=_WordsProvider())
        r = await svc.generate_answer(
            "问题",
            [{"content": "催收过程不得暴力催收、威胁上门", "doc_id": "d1", "category": "regulation"}],
            True,
        )
        assert r["answer"] == "请遵循文明催收规范，不得威胁客户"
        assert r["llm_called"] is True
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_post_redline_discard_answer(self, switch_on):
        """后置内容校验：回答命中红线 → 丢弃回答按降级处理。"""
        provider = FakeProvider(result=LLMAnswer("建议催收员暴力催收施压"))
        svc = LLMService(provider=provider, word_provider=_WordsProvider())
        r = await svc.generate_answer("问题", [{"content": "制度正文"}], True)
        assert r["answer"] is None
        assert r["degraded"] is True
        assert r["llm_called"] is True
        assert r["llm_skip_reason"] == "post_redline_blocked"
        assert len(provider.calls) == 1

    @pytest.mark.asyncio
    async def test_sensitive_masked_before_injection(self, switch_on):
        """敏感信息脱敏后注入 LLM（手机号/身份证/金额 → 占位符），不传明文。"""
        provider = FakeProvider(result=LLMAnswer("ok"))
        svc = LLMService(provider=provider)
        ctx = [{
            "content": "客户电话 13812345678，身份证 110101199001011234，欠款 5000 元",
            "doc_id": "d1",
        }]
        await svc.generate_answer("问题", ctx, True)
        injected = provider.calls[0][0][0]["content"]
        assert "13812345678" not in injected
        assert "110101199001011234" not in injected
        assert "[手机号]" in injected
        assert "[身份证号]" in injected
        assert "[金额]" in injected


class TestPromptTemplate:
    def test_prompt_loaded_from_yaml_params_injected(self):
        """Prompt 从 conf/llm_answer_prompt.yaml 加载，业务变量参数注入（无硬编码）。"""
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
        """缓存命中但 answer=true：跳过缓存读取走真实检索，且不写缓存。"""
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
        assert embed.calls == 1  # 跳过缓存，真实检索
        assert fake.calls["exists"] == 0  # 未读缓存
        assert fake.calls["setex"] == 0  # 未写缓存

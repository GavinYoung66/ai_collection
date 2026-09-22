"""QC-5.2 / QC-5.3：LLMReviewer 非法 turn_id 丢弃、豁免落日志、熔断 A2011。"""
from __future__ import annotations

import json

import pytest

from app.core.config import qc_settings
from app.events.publisher import CHANNEL_SYSTEM_ALERT, FORBIDDEN_CHANNELS, QcEventPublisher
from app.repositories.job_store import QcJobStore, llm_chain_models, llm_degraded_key
from app.services.llm_reviewer import LLMReviewer, turns_payload
from app.services.offline_qc_service import OfflineQcService
from common.llm import reliability as reliability_mod
from common.resilience._redis import configure_redis, reset_redis
from tests.test_job_store import MemoryRedis

GLOBAL_LLM_KEY = "qc:llm_degraded"


@pytest.fixture(autouse=True)
def _isolate_llm_reliability(monkeypatch):
    redis = MemoryRedis(db=3)
    configure_redis(redis)
    reliability_mod.set_default_service("qc")
    reliability_mod._llm_breakers.clear()
    monkeypatch.setattr(reliability_mod, "_LLM_RETRY_DELAY", 0)
    yield redis
    reliability_mod._llm_breakers.clear()
    reliability_mod.set_default_service(None)
    reset_redis()


def _turns():
    return [
        {"turn_id": "t1", "speaker": "agent", "text": "您好，请尽快还款", "turn_index": 1},
        {"turn_id": "t2", "speaker": "user", "text": "我要投诉你们", "turn_index": 2},
    ]


def _hit_json(**kwargs) -> str:
    row = {
        "level": "high",
        "rule_id": "R12",
        "redline_tag": "fake_identity",
        "evidence": {"turn_id": "t1", "text": "您好，请尽快还款"},
        "reason": "坐席施压催收",
        "confidence": 0.1,
    }
    row.update(kwargs)
    return json.dumps([row], ensure_ascii=False)


def _offline_payload(session_id: str = "sess_llm") -> dict:
    return {
        "session_id": session_id,
        "call_ended": True,
        "text_ready": True,
        "debtor_name": "张三",
        "turns": [
            {
                "turn_id": "t1",
                "speaker": "agent",
                "text": "让你妈妈接电话",
                "turn_index": 1,
            }
        ],
        "result": None,
    }


class TestLLMReviewer:
    async def test_unknown_turn_id_dropped_keeps_valid(self):
        payload = json.dumps(
            [
                {
                    "level": "high",
                    "rule_id": "R12",
                    "redline_tag": "fake_identity",
                    "evidence": {"turn_id": "t-missing", "text": "ghost"},
                    "reason": "幻觉轮次",
                    "confidence": 0.99,
                },
                {
                    "level": "medium",
                    "rule_id": "R11",
                    "redline_tag": "threat",
                    "evidence": {"turn_id": "t1", "text": "尽快还款"},
                    "reason": "坐席催收施压",
                    "confidence": 0.2,
                },
            ],
            ensure_ascii=False,
        )

        async def _invoke(_messages):
            return payload

        reviewer = LLMReviewer(invoke=_invoke)
        hits = await reviewer.review(_turns(), session_id="s1")
        assert [h.turn_id for h in hits] == ["t1"]
        assert hits[0].rule_id == "R11"
        assert hits[0].extras["confidence"] == 0.2
        assert reviewer.call_logs[-1]["status"] == "qc_review_exemption"
        assert "0.7" not in str(reviewer.call_logs)

    async def test_low_confidence_not_gated(self):
        async def _invoke(_messages):
            return _hit_json(confidence=0.05)

        hits = await LLMReviewer(invoke=_invoke).review(_turns())
        assert len(hits) == 1

    async def test_fabricated_amount_dropped(self):
        async def _invoke(_messages):
            return _hit_json(reason="客户欠款3800元必须还清")

        hits = await LLMReviewer(invoke=_invoke).review(_turns())
        assert hits == []

    async def test_user_complaint_not_agent_violation(self):
        async def _invoke(_messages):
            return _hit_json(
                rule_id="R13",
                redline_tag="stop_call",
                evidence={"turn_id": "t2", "text": "我要投诉你们"},
                reason="用户投诉",
            )

        hits = await LLMReviewer(invoke=_invoke).review(_turns())
        assert hits == []

    async def test_prompt_omits_overdue_amount(self):
        captured: list[list[dict]] = []

        async def _invoke(messages):
            captured.append(messages)
            return "[]"

        extra = list(_turns())
        extra[0] = {**extra[0], "due_amount": 3800.0, "overdue_days": 32}
        await LLMReviewer(invoke=_invoke).review(extra)
        blob = json.dumps(captured, ensure_ascii=False)
        assert "due_amount" not in blob
        assert "overdue_days" not in blob
        assert "3800" not in blob
        rows = turns_payload(extra)
        assert "due_amount" not in rows[0]

    async def test_does_not_emit_r10_unverified_from_llm(self):
        async def _invoke(_messages):
            return _hit_json(rule_id="R10", redline_tag="unverified", reason="缺护栏日志")

        hits = await LLMReviewer(invoke=_invoke).review(_turns())
        assert hits == []


class TestQcMockLlm:
    async def test_mock_flag_skips_invoke_without_breaker(self, monkeypatch):
        monkeypatch.setattr("app.services.llm_reviewer.qc_settings.QC_MOCK_LLM", True)
        calls = {"n": 0}

        async def _invoke(_messages):
            calls["n"] += 1
            return "[]"

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        reviewer = LLMReviewer(invoke=_invoke, job_store=store)
        hits = await reviewer.review(_turns())
        assert hits == []
        assert calls["n"] == 0
        assert reviewer.degraded is False
        assert reviewer.call_logs[-1]["status"] == "qc_review_exemption"
        assert reviewer.call_logs[-1]["error_msg"] == "qc_mock_llm"
        assert GLOBAL_LLM_KEY not in redis.data
        assert await redis.get(llm_degraded_key(qc_settings.QC_LLM_MODEL)) is None
        result = await OfflineQcService(llm=reviewer, job_store=store).run(
            _offline_payload("sess_mock")
        )
        assert result.code == "0"
        assert result.compliance_score == 80


class TestLlmInvokeA2011:
    async def test_primary_fail_fallback_keeps_json_object(self, monkeypatch):
        captured: list[dict] = []

        async def fake_post(**kwargs):
            captured.append(
                {
                    "model": kwargs["model"],
                    "response_format": kwargs.get("response_format"),
                }
            )
            if kwargs["model"] == qc_settings.QC_LLM_MODEL:
                raise TimeoutError("primary timeout")
            return {"choices": [{"message": {"content": "[]"}}]}

        monkeypatch.setattr("app.services.llm_reviewer.post_chat_completions", fake_post)
        monkeypatch.setattr("app.services.llm_reviewer.require_real_secret", lambda *a, **k: "sk-test")
        reviewer = LLMReviewer()
        hits = await reviewer.review(_turns())
        assert hits == []
        assert reviewer.degraded is False
        models = [row["model"] for row in captured]
        assert models[0] == qc_settings.QC_LLM_MODEL
        assert qc_settings.QC_LLM_FALLBACK_CHAIN[0] in models
        assert all(row["response_format"] == {"type": "json_object"} for row in captured)

    async def test_whole_chain_timeout_a2011_scores_not_500(self, _isolate_llm_reliability):
        redis = _isolate_llm_reliability
        configure_redis(redis)
        calls: list[str] = []

        async def _boom(model, messages, timeout_seconds):
            calls.append(model)
            raise TimeoutError("timeout")

        store = QcJobStore(redis)
        publisher = QcEventPublisher()
        reviewer = LLMReviewer(invoke=_boom, job_store=store, publisher=publisher)
        result = await OfflineQcService(llm=reviewer, job_store=store, publisher=publisher).run(
            _offline_payload()
        )
        envelope = result.to_envelope()
        assert result.code == "A2011"
        assert envelope["data"] is not None
        assert envelope["data"]["compliance_score"] == 80
        assert envelope["data"]["qc_status"] != "PARTIAL"
        assert result.qc_status != "PARTIAL"
        assert qc_settings.QC_LLM_MODEL in calls
        assert qc_settings.QC_LLM_FALLBACK_CHAIN[0] in calls
        assert calls.count(qc_settings.QC_LLM_MODEL) == 1
        primary_key = llm_degraded_key(qc_settings.QC_LLM_MODEL)
        backup_key = llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])
        assert await redis.get(primary_key) == "1"
        assert await redis.get(backup_key) == "1"
        assert redis.ttl.get(primary_key) == 60
        assert redis.ttl.get(backup_key) == 60
        assert GLOBAL_LLM_KEY not in redis.data
        assert await store.is_llm_chain_degraded() is True
        channels = [ch for ch, _ in publisher.emitted]
        assert CHANNEL_SYSTEM_ALERT in channels
        assert "qc.llm.degraded" not in channels
        payload = publisher.emitted[0][1]
        assert payload["event"] == "qc:llm_degraded"
        assert payload["error_code"] == "A2011"
        assert payload["service"] == "qc-service"
        assert FORBIDDEN_CHANNELS.isdisjoint(set(channels))

    async def test_llm_rate_limit_error_a2011_not_500(self, monkeypatch, _isolate_llm_reliability):
        redis = _isolate_llm_reliability
        configure_redis(redis)
        monkeypatch.setattr(reliability_mod, "_LLM_RATE_LIMIT", 0)
        calls = {"n": 0}

        async def _ok(model, messages, timeout_seconds):
            calls["n"] += 1
            return "[]"

        store = QcJobStore(redis)
        publisher = QcEventPublisher()
        reviewer = LLMReviewer(invoke=_ok, job_store=store, publisher=publisher)
        result = await OfflineQcService(llm=reviewer, job_store=store, publisher=publisher).run(
            _offline_payload("sess_rl")
        )
        assert result.code == "A2011"
        assert result.to_envelope()["data"]["compliance_score"] == 80
        assert result.qc_status != "PARTIAL"
        assert calls["n"] == 0
        for model in llm_chain_models():
            key = llm_degraded_key(model)
            assert await redis.get(key) == "1"
            assert redis.ttl.get(key) == 60
        assert GLOBAL_LLM_KEY not in redis.data
        assert await store.is_llm_chain_degraded() is True
        channels = [ch for ch, _ in publisher.emitted]
        assert CHANNEL_SYSTEM_ALERT in channels

    async def test_writes_per_model_key_not_global(self, _isolate_llm_reliability):
        redis = _isolate_llm_reliability
        configure_redis(redis)

        async def _boom(model, messages, timeout_seconds):
            raise TimeoutError("timeout")

        reviewer = LLMReviewer(
            invoke=_boom, job_store=QcJobStore(redis), publisher=QcEventPublisher()
        )
        await reviewer.review(_turns())
        assert reviewer.degraded is True
        for model in llm_chain_models():
            assert await redis.get(llm_degraded_key(model)) == "1"
        assert GLOBAL_LLM_KEY not in redis.data

    async def test_primary_open_does_not_block_backup(self, _isolate_llm_reliability):
        redis = _isolate_llm_reliability
        configure_redis(redis)
        primary = reliability_mod._llm_breakers.get_or_create(
            llm_degraded_key(qc_settings.QC_LLM_MODEL)
        )
        for _ in range(3):
            await primary.record_failure()
        calls: list[str] = []

        async def _invoke(model, messages, timeout_seconds):
            calls.append(model)
            return "[]"

        reviewer = LLMReviewer(invoke=_invoke, job_store=QcJobStore(redis))
        hits = await reviewer.review(_turns())
        assert hits == []
        assert reviewer.degraded is False
        assert qc_settings.QC_LLM_MODEL not in calls
        assert calls == [qc_settings.QC_LLM_FALLBACK_CHAIN[0]]
        assert GLOBAL_LLM_KEY not in redis.data
        assert await redis.get(llm_degraded_key(qc_settings.QC_LLM_MODEL)) == "1"
        assert await redis.get(llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])) is None
        store = QcJobStore(redis)
        assert await store.is_llm_chain_degraded() is False

    async def test_success_clears_that_model_key_not_others(self, _isolate_llm_reliability):
        redis = _isolate_llm_reliability
        configure_redis(redis)
        primary_key = llm_degraded_key(qc_settings.QC_LLM_MODEL)
        backup_key = llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])
        await redis.setex(primary_key, 60, "1")
        await redis.setex(backup_key, 60, "1")

        async def _ok(model, messages, timeout_seconds):
            return "[]"

        reviewer = LLMReviewer(invoke=_ok, job_store=QcJobStore(redis))
        await reviewer.review(_turns())
        assert reviewer.degraded is False
        assert await redis.get(primary_key) is None
        assert await redis.get(backup_key) == "1"
        assert GLOBAL_LLM_KEY not in redis.data

    async def test_backup_success_does_not_clear_primary_key(self, _isolate_llm_reliability):
        redis = _isolate_llm_reliability
        configure_redis(redis)

        async def _invoke(model, messages, timeout_seconds):
            if model == qc_settings.QC_LLM_MODEL:
                raise TimeoutError("primary timeout")
            return "[]"

        reviewer = LLMReviewer(invoke=_invoke, job_store=QcJobStore(redis))
        await reviewer.review(_turns())
        assert reviewer.degraded is False
        assert await redis.get(llm_degraded_key(qc_settings.QC_LLM_MODEL)) == "1"
        assert await redis.get(llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])) is None
        assert GLOBAL_LLM_KEY not in redis.data

    async def test_half_open_probe_success_clears_that_model_key(self, _isolate_llm_reliability):
        from common.config.settings import settings as platform_settings

        redis = _isolate_llm_reliability
        configure_redis(redis)
        now = {"t": 0.0}

        def clock() -> float:
            return now["t"]

        primary_key = llm_degraded_key(qc_settings.QC_LLM_MODEL)
        backup_key = llm_degraded_key(qc_settings.QC_LLM_FALLBACK_CHAIN[0])
        primary = reliability_mod._llm_breakers.get_or_create(primary_key, clock=clock)
        for _ in range(int(platform_settings.LLM_CIRCUIT_BREAKER_THRESHOLD)):
            await primary.record_failure()
        assert primary.state == "open"
        await redis.setex(primary_key, 60, "1")
        await redis.setex(backup_key, 60, "1")

        now["t"] = float(platform_settings.LLM_CIRCUIT_BREAKER_RECOVERY) + 1.0
        calls: list[str] = []

        async def _ok(model, messages, timeout_seconds):
            calls.append(model)
            return "[]"

        reviewer = LLMReviewer(invoke=_ok, job_store=QcJobStore(redis))
        await reviewer.review(_turns())
        assert reviewer.degraded is False
        assert calls[0] == qc_settings.QC_LLM_MODEL
        assert primary.state == "closed"
        assert await redis.get(primary_key) is None
        assert await redis.get(backup_key) == "1"
        assert GLOBAL_LLM_KEY not in redis.data

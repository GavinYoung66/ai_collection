"""QC-5.2 / QC-5.3：LLMReviewer 非法 turn_id 丢弃、豁免落日志、熔断 A2011。"""
from __future__ import annotations

import json

from app.events.publisher import CHANNEL_SYSTEM_ALERT, FORBIDDEN_CHANNELS, QcEventPublisher
from app.repositories.job_store import KEY_LLM_DEGRADED, QcJobStore
from app.services.llm_circuit import QcLlmCircuit
from app.services.llm_reviewer import LLMReviewer, turns_payload
from app.services.offline_qc_service import OfflineQcService
from tests.test_job_store import MemoryRedis


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
        assert await redis.get(KEY_LLM_DEGRADED) is None
        result = await OfflineQcService(llm=reviewer, job_store=store).run(
            {
                "session_id": "sess_mock",
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
        )
        assert result.code == "0"
        assert result.compliance_score == 80


class TestLlmCircuitA2011:
    async def test_timeout_opens_breaker_scores_remain_no_llm_channel(self):
        calls = {"n": 0}

        async def _boom(_messages):
            calls["n"] += 1
            raise RuntimeError("LLM HTTP 500: down")

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        publisher = QcEventPublisher()
        reviewer = LLMReviewer(
            invoke=_boom,
            job_store=store,
            publisher=publisher,
            retry_delay=0.0,
        )
        svc = OfflineQcService(llm=reviewer, job_store=store, publisher=publisher)
        result = await svc.run(
            {
                "session_id": "sess_llm",
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
        )
        assert result.code == "A2011"
        assert result.compliance_score == 80
        assert result.to_envelope()["data"] is not None
        assert result.to_envelope()["data"]["compliance_score"] == 80
        assert await redis.get(KEY_LLM_DEGRADED) == "1"
        assert calls["n"] == 3
        channels = [ch for ch, _ in publisher.emitted]
        assert CHANNEL_SYSTEM_ALERT in channels
        assert "qc.llm.degraded" not in channels
        payload = publisher.emitted[0][1]
        assert payload["event"] == "qc:llm_degraded"
        assert payload["error_code"] == "A2011"
        assert payload["service"] == "qc-service"
        assert set(payload) >= {
            "alert_id",
            "level",
            "event",
            "service",
            "description",
            "error_code",
            "trace_id",
            "ts",
        }
        assert FORBIDDEN_CHANNELS.isdisjoint(set(channels))

        hits = await reviewer.review(
            [{"turn_id": "t1", "speaker": "agent", "text": "您好", "turn_index": 1}]
        )
        assert hits == []
        assert reviewer.degraded is True
        assert calls["n"] == 3

    async def test_half_open_success_deletes_key(self):
        clock = {"t": 0.0}

        async def _ok(_messages):
            return "[]"

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        circuit = QcLlmCircuit(store, now=lambda: clock["t"], reset_timeout=60.0)
        await circuit.trip_open()
        assert await redis.get(KEY_LLM_DEGRADED) == "1"
        clock["t"] = 61.0
        assert circuit.should_skip() is False
        reviewer = LLMReviewer(invoke=_ok, job_store=store, circuit=circuit)
        await reviewer.review(
            [{"turn_id": "t1", "speaker": "agent", "text": "您好", "turn_index": 1}]
        )
        assert await redis.get(KEY_LLM_DEGRADED) is None
        assert circuit.state == "closed"

    async def test_half_open_invalid_json_keeps_degraded_key(self):
        clock = {"t": 0.0}

        async def _bad(_messages):
            return "{not json"

        redis = MemoryRedis(db=3)
        store = QcJobStore(redis)
        circuit = QcLlmCircuit(store, now=lambda: clock["t"], reset_timeout=60.0)
        await circuit.trip_open()
        clock["t"] = 61.0
        assert circuit.should_skip() is False
        reviewer = LLMReviewer(invoke=_bad, job_store=store, circuit=circuit)
        hits = await reviewer.review(
            [{"turn_id": "t1", "speaker": "agent", "text": "您好", "turn_index": 1}]
        )
        assert hits == []
        assert await redis.get(KEY_LLM_DEGRADED) == "1"
        assert circuit.state == "open"

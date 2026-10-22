"""common.llm 模块单元测试。"""
import json

import pytest
from pydantic import ValidationError

from common.llm.schemas.dialog_schema import DialogResponse, DialogTurn
from common.llm.schemas.intent_schema import INTENT_TYPES, IntentResult, IntentType
from common.llm.prompt_files import load_dialog_prompt, load_fallback_scripts
from common.llm.rule_engine import RuleBasedScriptEngine


class TestDialogSchema:
    def test_valid_dialog_response(self):
        resp = DialogResponse(
            reasoning="客户有还款意愿",
            answer="请您尽快完成还款",
            confidence=0.85,
            intent="willing_to_pay",
        )
        assert resp.answer == "请您尽快完成还款"
        assert resp.confidence == 0.85

    def test_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            DialogResponse(
                reasoning="",
                answer="test",
                confidence=1.5,
                intent="uncertain",
            )

    def test_confidence_negative(self):
        with pytest.raises(ValidationError):
            DialogResponse(
                reasoning="",
                answer="test",
                confidence=-0.1,
                intent="uncertain",
            )

    def test_empty_answer(self):
        with pytest.raises(ValidationError):
            DialogResponse(reasoning="", answer="", confidence=0.5, intent="uncertain")

    def test_answer_too_long(self):
        with pytest.raises(ValidationError):
            DialogResponse(reasoning="", answer="x" * 501, confidence=0.5, intent="uncertain")

    def test_dialog_turn_valid(self):
        turn = DialogTurn(speaker="ai", text="您好", turn_number=1)
        assert turn.speaker == "ai"
        assert turn.turn_number == 1

    def test_dialog_turn_invalid_speaker(self):
        with pytest.raises(ValidationError):
            DialogTurn(speaker="bot", text="您好", turn_number=1)


class TestIntentSchema:
    def test_intent_type_count(self):
        assert len(INTENT_TYPES) == 8

    def test_intent_types_values(self):
        expected = {
            "willing_to_pay", "extension_request", "financial_difficulty",
            "stalling", "rejection", "anger", "complaint", "uncertain",
        }
        assert set(INTENT_TYPES) == expected

    def test_intent_result_valid(self):
        result = IntentResult(
            intent=IntentType.WILLING_TO_PAY,
            confidence=0.9,
            keywords=["还", "马上"],
            reasoning="客户表示愿意还款",
        )
        assert result.intent == IntentType.WILLING_TO_PAY
        assert result.confidence == 0.9

    def test_intent_result_confidence_out_of_range(self):
        with pytest.raises(ValidationError):
            IntentResult(intent=IntentType.ANGER, confidence=1.5)


class TestPromptsLoader:
    def test_load_dialog_prompt(self):
        prompt = load_dialog_prompt()
        assert "system" in prompt
        assert "user_template" in prompt
        assert "few_shot" in prompt

    def test_load_fallback_scripts(self):
        scripts = load_fallback_scripts()
        assert len(scripts) > 0
        assert all("category" in s for s in scripts)
        assert all("response" in s for s in scripts)

    def test_fallback_scripts_categories(self):
        scripts = load_fallback_scripts()
        categories = {s["category"] for s in scripts}
        assert "opening" in categories
        assert "willing_to_pay" in categories
        assert "uncertain" in categories
        assert "system_error" in categories


class TestRuleEngine:
    @pytest.fixture()
    def engine(self):
        e = RuleBasedScriptEngine()
        e.load_defaults()
        return e

    def test_fallback_willing_to_pay(self, engine):
        resp = engine.fallback_response("我要还款")
        assert "还款" in resp

    def test_fallback_extension_request(self, engine):
        resp = engine.fallback_response("能不能宽限几天")
        assert "宽限" in resp or "延期" in resp or "预计" in resp

    def test_fallback_anger(self, engine):
        resp = engine.fallback_response("你们别打了，我要投诉")
        assert "投诉" in resp or "打扰" in resp

    def test_fallback_no_match(self, engine):
        resp = engine.fallback_response("嗯哼")
        assert len(resp) > 0

    def test_fallback_system_error(self, engine):
        resp = engine.fallback_response("系统错误", category="system_error")
        assert len(resp) > 0

    def test_load_custom_rules(self):
        e = RuleBasedScriptEngine()
        e.load_rules([{"category": "test", "keywords": ["hello"], "response": "hi"}])
        resp = e.fallback_response("hello world")
        assert resp == "hi"

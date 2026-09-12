"""QC-4.3 / QC-4.4 / QC-4.5：RuleEngine skip 表、R04、R14；无 R10。"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from app.core.config import qc_settings
from app.integrations.qc_session import QCSession
from app.seeds.qc_rules_v1 import V1_SEED
from app.services.rule_engine import RULES_IN_ROUND, RuleEngine

_APP = Path(__file__).resolve().parents[1] / "app"


def _turns(*rows: tuple[str, str, int]) -> list[dict]:
    out = []
    for speaker, text, index in rows:
        out.append(
            {
                "turn_id": f"t{index}",
                "speaker": speaker,
                "text": text,
                "turn_index": index,
            }
        )
    return out


def _session(**kwargs) -> dict:
    base = {
        "session_id": "sess_rules",
        "call_ended": True,
        "turns": _turns(("agent", "您好", 1)),
    }
    base.update(kwargs)
    return base


class TestSkipTable:
    def test_missing_debtor_name_skips_r01(self):
        engine = RuleEngine()
        result = engine.run(_session(debtor_name=None, turns=_turns(("agent", "让你妈妈接电话", 1))))
        assert "R01" in result.skipped_rules
        assert not any(hit.rule_id == "R01" for hit in result.hits)
        assert "R10" not in result.skipped_rules
        assert not any(hit.rule_id == "R10" for hit in result.hits)
        assert not any(hit.redline_tag == "unverified" for hit in result.hits)

    def test_r01_hits_when_name_present(self):
        result = RuleEngine().run(
            _session(debtor_name="张三", turns=_turns(("agent", "让你妈妈接电话", 1)))
        )
        assert "R01" not in result.skipped_rules
        assert any(hit.rule_id == "R01" for hit in result.hits)

    def test_r05_does_not_guess_from_turns(self):
        many = [( "agent" if i % 2 else "user", f"第{i}轮", i) for i in range(1, 12)]
        result = RuleEngine().run(_session(turns=_turns(*many)))
        assert "R05" in result.skipped_rules
        assert not any(hit.rule_id == "R05" for hit in result.hits)

    def test_r03_skips_without_snapshot(self):
        result = RuleEngine().run(_session())
        assert "R03" in result.skipped_rules

    def test_r08_skips_when_ptp_null(self):
        result = RuleEngine().run(
            QCSession.model_validate(
                {
                    "session_id": "s",
                    "call_ended": True,
                    "turns": _turns(("agent", "您好", 1)),
                    "case_snapshot": {"due_amount": 3800.0, "overdue_days": 32, "strategy_refs": []},
                }
            )
        )
        assert "R08" in result.skipped_rules

    def test_skip_is_not_partial_or_passed_redline(self):
        result = RuleEngine().run(_session(debtor_name=None))
        assert not hasattr(result, "qc_status") or result.qc_status != "PARTIAL"
        assert result.skipped_rules
        assert "红线已通过" not in str(result)


class TestR04:
    def test_disclosure_before_confirm_hits(self):
        result = RuleEngine().run(
            _session(
                turns=_turns(
                    ("agent", "你欠款逾期了", 1),
                    ("user", "啊？", 2),
                )
            )
        )
        r04 = [hit for hit in result.hits if hit.rule_id == "R04"]
        assert r04
        assert r04[0].severity == "medium"
        assert r04[0].redline_tag == "privacy_leak"

    def test_disclosure_after_shiwo_does_not_hit(self):
        result = RuleEngine().run(
            _session(
                turns=_turns(
                    ("agent", "请问是张先生吗", 1),
                    ("user", "是我", 2),
                    ("agent", "你欠款逾期了要还", 3),
                )
            )
        )
        assert not any(hit.rule_id == "R04" for hit in result.hits)

    def test_does_not_depend_on_debtor_name(self):
        result = RuleEngine().run(
            _session(debtor_name=None, turns=_turns(("agent", "你账单未还", 1)))
        )
        assert "R04" not in result.skipped_rules
        assert any(hit.rule_id == "R04" for hit in result.hits)

    def test_impersonation_goes_to_r12_not_r04(self):
        result = RuleEngine().run(
            _session(turns=_turns(("agent", "我是法院的，过来配合调查", 1)))
        )
        assert any(hit.rule_id == "R12" and hit.redline_tag == "fake_identity" for hit in result.hits)
        assert not any(hit.rule_id == "R04" for hit in result.hits)

    def test_id_question_bushi_then_disclosure_hits(self):
        result = RuleEngine().run(
            _session(
                turns=_turns(
                    ("agent", "请问是张先生吗", 1),
                    ("user", "不是", 2),
                    ("agent", "你欠款逾期了", 3),
                )
            )
        )
        assert any(hit.rule_id == "R04" for hit in result.hits)

    def test_id_question_budui_then_disclosure_hits(self):
        result = RuleEngine().run(
            _session(
                turns=_turns(
                    ("agent", "请问是张先生吗", 1),
                    ("user", "不对", 2),
                    ("agent", "你欠款逾期了", 3),
                )
            )
        )
        assert any(hit.rule_id == "R04" for hit in result.hits)

    def test_id_question_short_yes_then_disclosure_does_not_hit(self):
        for reply in ("是", "没错", "是的"):
            result = RuleEngine().run(
                _session(
                    turns=_turns(
                        ("agent", "请问是张先生吗", 1),
                        ("user", reply, 2),
                        ("agent", "你欠款逾期了", 3),
                    )
                )
            )
            assert not any(hit.rule_id == "R04" for hit in result.hits), reply


def _r14_pack(*, keywords: list[str]) -> dict:
    pack = deepcopy(V1_SEED)
    for item in pack["rules"]:
        if item["rule_id"] == "R14":
            item["params"]["keywords"] = list(keywords)
    return pack


class TestR14:
    def test_empty_keywords_and_no_hit_text_skips(self):
        pack = _r14_pack(keywords=[])
        result = RuleEngine(pack).run(_session(turns=_turns(("agent", "不还就起诉", 1))))
        assert "R14" in result.skipped_rules
        assert not any(hit.rule_id == "R14" for hit in result.hits)

    def test_seed_keyword_hits_without_search(self):
        result = RuleEngine().run(_session(turns=_turns(("agent", "再不还就起诉你", 1))))
        r14 = [hit for hit in result.hits if hit.rule_id == "R14"]
        assert r14
        assert r14[0].severity == "medium"
        assert r14[0].redline_tag == "legal_overstate"
        assert qc_settings.QC_R14_RAG_ENABLED is False
        assert qc_settings.QC_ALERT_LEVEL_MIN == "high"

    def test_never_calls_kb_search(self):
        src = (_APP / "services" / "rule_engine.py").read_text(encoding="utf-8")
        assert "/kb/search" not in src
        assert "RagClient" not in src
        assert "guardrail.pass" not in src
        assert qc_settings.QC_R14_RAG_ENABLED is False
        RuleEngine().run(_session(turns=_turns(("agent", "强制执行", 1))))
        RuleEngine(_r14_pack(keywords=[])).run(_session())

    def test_hit_text_contained_no_r14(self):
        result = RuleEngine(_r14_pack(keywords=["起诉"])).run(
            _session(
                case_snapshot={"standard_answer_hit": "标准还款说明"},
                turns=_turns(("agent", "请听标准还款说明然后协商", 1)),
            )
        )
        assert "R14" not in result.skipped_rules
        assert not any(hit.rule_id == "R14" for hit in result.hits)

    def test_hit_text_missing_keywords_without_match_no_hit(self):
        result = RuleEngine(_r14_pack(keywords=["起诉"])).run(
            _session(
                case_snapshot={"standard_answer_hit": "标准还款说明"},
                turns=_turns(("agent", "您好请尽快处理", 1)),
            )
        )
        assert "R14" not in result.skipped_rules
        assert not any(hit.rule_id == "R14" for hit in result.hits)

    def test_hit_text_missing_keyword_match_hits(self):
        result = RuleEngine(_r14_pack(keywords=["起诉"])).run(
            _session(
                case_snapshot={"standard_answer_hit": "标准还款说明"},
                turns=_turns(("agent", "再不还就起诉你", 1)),
            )
        )
        r14 = [hit for hit in result.hits if hit.rule_id == "R14"]
        assert r14
        assert r14[0].severity == "medium"

    def test_hit_text_missing_empty_keywords_hits_miss(self):
        result = RuleEngine(_r14_pack(keywords=[])).run(
            _session(
                case_snapshot={"standard_answer_hit": "标准还款说明"},
                turns=_turns(("agent", "您好请尽快处理", 1)),
            )
        )
        assert "R14" not in result.skipped_rules
        assert any(hit.rule_id == "R14" for hit in result.hits)


class TestR13AndNoR10:
    def test_user_row_not_score_eligible(self):
        result = RuleEngine().run(
            _session(
                turns=_turns(
                    ("user", "你们不要再打了", 1),
                    ("agent", "今天必须还钱", 2),
                )
            )
        )
        user_hits = [hit for hit in result.hits if hit.rule_id == "R13" and hit.speaker == "user"]
        agent_hits = [hit for hit in result.hits if hit.rule_id == "R13" and hit.speaker == "agent"]
        assert user_hits
        assert all(hit.score_eligible is False for hit in user_hits)
        assert agent_hits
        assert all(hit.score_eligible is True for hit in agent_hits)

    def test_round_does_not_evaluate_r10(self):
        assert "R10" not in RULES_IN_ROUND
        result = RuleEngine().run(_session(result=None, debtor_name=None))
        assert "R10" not in result.skipped_rules
        assert not any(hit.rule_id == "R10" for hit in result.hits)

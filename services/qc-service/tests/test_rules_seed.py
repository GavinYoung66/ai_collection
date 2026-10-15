"""QC-2.5：v1.0 规则种子含 R14 十八词与 deduction_policy，全库仅一版 active 口径。"""
from app.core.config import qc_settings
from app.deps import get_active_qc_rules
from app.seeds.qc_rules_v1 import (
    DEDUCTION_POLICY_V1,
    FORBIDDEN_R14_KEYWORD,
    PENALTY_V1,
    QC_RULES_VERSION_V1,
    R14_KEYWORDS,
    SAME_WORD_WINDOW_MS_V1,
    V1_RULES,
    V1_SEED,
)


class TestRulesSeedV1:
    def test_default_version_flag(self):
        assert qc_settings.QC_DEFAULT_ACTIVE_RULES_VERSION == QC_RULES_VERSION_V1 == "v1.0"
        assert V1_SEED["status"] == "active"
        assert V1_SEED["version"] == "v1.0"

    def test_penalty_window_and_no_grade_thresholds(self):
        assert V1_SEED["penalty"] == PENALTY_V1 == {"high": 20, "medium": 8, "low": 2}
        assert V1_SEED["same_word_window_ms"] == SAME_WORD_WINDOW_MS_V1 == 30000
        assert "grade_thresholds" not in V1_SEED

    def test_deduction_policy_covers_all_rules(self):
        policy = V1_SEED["deduction_policy"]
        assert policy is DEDUCTION_POLICY_V1
        rule_ids = {r["rule_id"] for r in V1_RULES}
        assert rule_ids == {f"R{i:02d}" for i in range(1, 16)}
        for rid in rule_ids:
            entry = policy[rid]
            assert set(entry) == {"clause_no", "clause_text"}
            assert entry["clause_no"] and entry["clause_text"]
        default = policy["__ac_default__"]
        assert set(default["by_redline_tag"]) == {
            "threat", "fake_identity", "stop_call", "fake_commit",
            "legal_overstate", "unverified", "data_fabrication", "privacy_leak",
        }
        assert set(default["by_severity"]) == {"high", "medium", "low"}
        for node in list(default["by_redline_tag"].values()) + list(default["by_severity"].values()):
            assert node["clause_no"] and node["clause_text"]

    def test_r14_eighteen_keywords_exact(self):
        expected = [
            "起诉",
            "立案",
            "强制执行",
            "拘留",
            "坐牢",
            "判刑",
            "刑事责任",
            "逮捕",
            "征信花了",
            "征信洗白",
            "上黑名单",
            "失信被执行",
            "限制高消费",
            "追究法律责任",
            "冻结银行卡",
            "影响子女",
            "断供坐牢",
            "无法贷款",
        ]
        assert list(R14_KEYWORDS) == expected
        assert len(R14_KEYWORDS) == 18
        r14 = next(r for r in V1_SEED["rules"] if r["rule_id"] == "R14")
        assert r14["params"]["keywords"] == expected
        assert FORBIDDEN_R14_KEYWORD not in r14["params"]["keywords"]
        blob = str(V1_SEED)
        assert FORBIDDEN_R14_KEYWORD not in blob

    def test_snapshot_uses_v1_seed(self):
        snap = get_active_qc_rules()
        assert snap["version"] == "v1.0"
        assert snap["status"] == "active"
        r14 = next(r for r in snap["rules"] if r["rule_id"] == "R14")
        assert r14["params"]["keywords"] == list(R14_KEYWORDS)

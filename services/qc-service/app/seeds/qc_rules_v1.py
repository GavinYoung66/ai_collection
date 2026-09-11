"""qc_rules v1.0 默认 active 包（DD-02 §5.2.3 R14 十八词 + penalty/window/grade）。"""
from __future__ import annotations

from typing import Any

R14_KEYWORDS: tuple[str, ...] = (
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
)

FORBIDDEN_R14_KEYWORD = "我是法院的"

PENALTY_V1: dict[str, int] = {"high": 20, "medium": 8, "low": 2}
GRADE_THRESHOLDS_V1: dict[str, int] = {
    "EXCELLENT": 90,
    "PASS": 80,
    "WARNING": 60,
    "SERIOUS": 0,
}
SAME_WORD_WINDOW_MS_V1 = 30000
QC_RULES_VERSION_V1 = "v1.0"

_RULE_META: tuple[tuple[str, str, str, str | None], ...] = (
    ("R01", "keyword", "high", "privacy_leak"),
    ("R02", "duration", "medium", None),
    ("R03", "keyword", "medium", "fake_commit"),
    ("R04", "keyword", "medium", "privacy_leak"),
    ("R05", "keyword", "low", "threat"),
    ("R06", "silence", "low", None),
    ("R07", "duration", "medium", None),
    ("R08", "keyword", "medium", "fake_commit"),
    ("R09", "duration", "low", None),
    ("R10", "semantic", "high", "unverified"),
    ("R11", "keyword", "medium", "threat"),
    ("R12", "keyword", "high", "fake_identity"),
    ("R13", "keyword", "high", "stop_call"),
    ("R14", "keyword", "medium", "legal_overstate"),
    ("R15", "keyword", "high", "data_fabrication"),
)


def _rule_entry(rule_id: str, rule_type: str, severity: str, redline_tag: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if rule_id == "R14":
        params = {"keywords": list(R14_KEYWORDS)}
    entry: dict[str, Any] = {
        "rule_id": rule_id,
        "type": rule_type,
        "enabled": True,
        "severity": severity,
        "params": params,
    }
    if redline_tag is not None:
        entry["redline_tag"] = redline_tag
    return entry


V1_RULES: list[dict[str, Any]] = [_rule_entry(*row) for row in _RULE_META]

V1_SEED: dict[str, Any] = {
    "version": QC_RULES_VERSION_V1,
    "status": "active",
    "rules": V1_RULES,
    "penalty": PENALTY_V1,
    "same_word_window_ms": SAME_WORD_WINDOW_MS_V1,
    "grade_thresholds": GRADE_THRESHOLDS_V1,
    "description": "M1 default qc_rules package",
}

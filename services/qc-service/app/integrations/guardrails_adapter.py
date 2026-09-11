"""AC 词三分类适配。禁止读 call 护栏放行日志。"""
from __future__ import annotations

from common.security.sensitive_words import SensitiveWordMatcher

CATEGORY_TO_SEVERITY: dict[str, str] = {
    "threat": "high",
    "abuse": "medium",
    "induce": "low",
}


def classify_category(category: str | None) -> tuple[str, str]:
    """threat=high / abuse=medium / induce=low。返回 (severity, redline_tag)。"""
    raw = (category or "induce").strip().lower()
    if raw not in CATEGORY_TO_SEVERITY:
        raw = "induce"
    return CATEGORY_TO_SEVERITY[raw], raw


def matcher_from_words(words: list[str]) -> SensitiveWordMatcher:
    """复用 common.security.sensitive_words，禁止平行再写自动机。"""
    matcher = SensitiveWordMatcher()
    payload = [item for item in words if item]
    if payload:
        matcher.load_words(payload)
    return matcher

"""规则脚本引擎，LLM 降级兜底。"""
from __future__ import annotations

import structlog

from common.llm.prompts.loader import load_fallback_scripts

logger = structlog.get_logger()


class RuleBasedScriptEngine:
    """基于关键词匹配的规则引擎，LLM 不可用时降级使用。"""

    def __init__(self) -> None:
        self._rules: dict[str, list[dict]] = {}
        self._loaded = False

    def load_rules(self, rules: list[dict]) -> None:
        for rule in rules:
            category = rule.get("category", "default")
            self._rules.setdefault(category, []).append(rule)
        self._loaded = True

    def load_defaults(self) -> None:
        """从 fallback_scripts.yml 加载内置兜底话术。"""
        self.load_rules(load_fallback_scripts())

    def match(self, text: str, category: str = "default") -> list[dict]:
        if not self._loaded:
            self.load_defaults()
        hits: list[dict] = []
        for rule in self._rules.get(category, []):
            keywords = rule.get("keywords", [])
            if any(kw in text for kw in keywords):
                hits.append(rule)
        return hits

    def fallback_response(self, text: str, category: str = "uncertain") -> str:
        """降级兜底回复，先按文本关键词匹配，无命中则返回 category 默认话术。"""
        if not self._loaded:
            self.load_defaults()
        for cat_rules in self._rules.values():
            for rule in cat_rules:
                keywords = rule.get("keywords", [])
                if keywords and any(kw in text for kw in keywords):
                    return rule.get("response", "抱歉，系统暂时繁忙，请稍后再试。")
        for rule in self._rules.get(category, []):
            return rule.get("response", "抱歉，系统暂时繁忙，请稍后再试。")
        return "抱歉，系统暂时繁忙，请稍后再试。"


rule_engine = RuleBasedScriptEngine()

"""投诉研判规则兜底引擎。

扩展/包装 common.llm.rule_engine.RuleBasedScriptEngine，提供 6 个 generate_* 方法。
所有输出带 `[规则兜底]` 前缀，verdict 枚举四值（不含 normal）。
"""
from __future__ import annotations

from typing import Any

from common.llm.rule_engine import RuleBasedScriptEngine

_PREFIX = "[规则兜底]"

# verdict 四值枚举（不含 normal）
VERDICT_VALID = {"agent_violation", "user_misunderstanding", "compliant_but_poor_experience", "pending"}


class ComplaintRuleEngine:
    """投诉研判规则兜底引擎，包装 common RuleBasedScriptEngine。"""

    def __init__(self):
        self._engine = RuleBasedScriptEngine()
        self._engine.load_defaults()

    # ── 1. 反思报告：证据数量阈值校验 ──
    def generate_reflection_report(self, evidence_items: list[dict] | None = None, **kwargs: Any) -> dict:
        count = len(evidence_items or [])
        sufficient = count >= 3
        return {
            "summary": f"{_PREFIX} 证据数量={count}，{'满足' if sufficient else '不足'}阈值(3)",
            "sufficient": sufficient,
            "evidence_count": count,
        }

    # ── 2. 判责草稿：质检告警存在性判断 ──
    def generate_verdict_draft(self, qc_violations: list[dict] | None = None, **kwargs: Any) -> dict:
        has_violation = bool(qc_violations)
        verdict = "agent_violation" if has_violation else "user_misunderstanding"
        assert verdict in VERDICT_VALID
        return {
            "verdict": verdict,
            "confidence": 0.5,
            "reasoning": f"{_PREFIX} 基于质检告警存在性判断：{'有' if has_violation else '无'}违规告警",
        }

    # ── 3. 研判数据包：最小可用 ──
    def generate_judgment_package(self, **kwargs: Any) -> dict:
        return {
            "verdict": "pending",
            "confidence": 0.5,
            "response_draft": f"{_PREFIX} 系统降级中，请人工复核",
            "requires_human_review": True,
        }

    # ── 4. 合规意见：不引用法规 ──
    def generate_compliance_opinion(self, **kwargs: Any) -> dict:
        return {
            "compliance_conclusion": f"{_PREFIX} 规则兜底：无法引用法规，需人工合规审查",
            "regulation_refs": [],
            "requires_human_review": True,
        }

    # ── 5. 用户诉求：关键词匹配 ──
    def generate_user_demand(self, text: str = "", **kwargs: Any) -> dict:
        keywords = {
            "退款": "refund",
            "退钱": "refund",
            "道歉": "apology",
            "投诉": "complaint",
            "赔偿": "compensation",
        }
        demand = "unknown"
        for kw, tag in keywords.items():
            if kw in text:
                demand = tag
                break
        return {
            "demand_type": demand,
            "summary": f"{_PREFIX} 关键词匹配：{demand}",
        }

    # ── 6. 事实时序：时间戳排序 ──
    def generate_fact_timeline(self, events: list[dict] | None = None, **kwargs: Any) -> dict:
        sorted_events = sorted(events or [], key=lambda e: e.get("timestamp", ""))
        return {
            "events": sorted_events,
            "summary": f"{_PREFIX} 按时间戳排序，共 {len(sorted_events)} 条事件",
        }

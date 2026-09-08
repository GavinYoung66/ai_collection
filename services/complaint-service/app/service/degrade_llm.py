"""LLM 降级（9.2a）—— 3 场景。

1. LLM 判责失败 → RuleBasedScriptEngine（A5004）
2. 产出物生成失败 → 模板填充答辩初稿（A5010）
3. RAG 检索失败 → 规则兜底不引用法规（A5005）
降级标记写入 state.exception_info。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from common.errors.codes import ErrorCode

_TEMPLATE_RESPONSE = "经核实，您反馈的问题我们正在处理，将尽快与您联系。"


class DegradeLLM:
    @staticmethod
    def _exc(code: str, reason: str) -> dict:
        return {"code": code, "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat()}

    def handle_judge_failure(self, state: dict | None = None) -> dict:
        """LLM 判责失败 → RuleBased 最小判责。"""
        out = {
            "verdict": "pending",
            "confidence": 0.0,
            "response_draft": _TEMPLATE_RESPONSE,
            "exception_info": [self._exc(ErrorCode.A5004.value, "LLM 判责失败，RuleBased 兜底")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out

    def handle_output_failure(self, state: dict | None = None) -> dict:
        """产出物失败 → 模板填充。"""
        out = {
            "response_draft": _TEMPLATE_RESPONSE,
            "exception_info": [self._exc(ErrorCode.A5010.value, "产出物生成失败，模板填充")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out

    def handle_rag_failure(self, state: dict | None = None) -> dict:
        """RAG 失败 → 规则兜底不引用法规。"""
        out = {
            "verdict": "pending",
            "regulation_refs": [],
            "exception_info": [self._exc(ErrorCode.A5005.value, "RAG 检索失败，规则兜底")],
        }
        if state is not None:
            state.setdefault("exception_info", []).extend(out["exception_info"])
        return out


# 全局单例
degrade_llm = DegradeLLM()

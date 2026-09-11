"""QC-5.1：qc_review.yml version 与 §13.2 约束句。"""
from pathlib import Path

from common.llm.prompts.loader import load_qc_review_prompt

_YML = Path(__file__).resolve().parents[3] / "common" / "llm" / "prompts" / "qc_review.yml"


class TestQcReviewPrompt:
    def test_version_and_constraints(self):
        spec = load_qc_review_prompt()
        assert spec["version"] == "qc_review_v1"
        text = _YML.read_text(encoding="utf-8")
        assert "qc_review_v1" in text
        joined = "\n".join(spec.get("constraints") or [])
        prohibitions = "\n".join(spec.get("prohibitions") or [])
        assert "只判定 agent 违规" in joined
        assert "投诉、自杀、停呼" in joined
        assert "打 R13" in joined
        assert "不得因缺少护栏日志或未检索知识库而输出 R10/unverified 或 R14" in joined
        assert "turn_id" in joined
        assert "不得编造金额" in prohibitions
        assert "不得把客户自杀或投诉表述本身标为坐席违规" in prohibitions
        assert spec.get("fallback") == "[]"

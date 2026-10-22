"""QC-5.1：qc_review 模板 version 与 §13.2 约束句。"""
from pathlib import Path

from common.prompt.loader import load_prompt_sections

_YML = (
    Path(__file__).resolve().parents[3]
    / "services"
    / "qc-service"
    / "app"
    / "prompt"
    / "templates"
    / "qc_review.yaml"
)


class TestQcReviewPrompt:
    def test_version_and_constraints(self):
        spec = load_prompt_sections("qc_review", service="qc-service")
        assert spec["version"] == "1.0.0"
        assert spec["prompt_id"] == "qc_review"
        assert spec["profile"] == "standard"
        text = _YML.read_text(encoding="utf-8")
        assert "1.0.0" in text
        sections = spec["sections"]
        joined = "\n".join(sections.get("input_constraint") or [])
        prohibitions = "\n".join(sections.get("prohibitions") or [])
        assert "只判定 agent 违规" in joined
        assert "投诉、自杀、停呼" in joined
        assert "打 R13" in joined
        assert "不得因缺少护栏日志或未检索知识库而输出 R10/unverified 或 R14" in joined
        assert "turn_id" in joined
        assert "不得编造金额" in prohibitions
        assert "不得把客户自杀或投诉表述本身标为坐席违规" in prohibitions
        assert sections.get("fallback") == "[]"
        assert sections.get("few_shot") == []
        assert sections.get("enum_constraints", {}).get("level") == ["high", "medium", "low"]
        assert sections.get("task") == "在通话已结束后，根据完整对话检测坐席违规"
        assert sections.get("role") == "你是催收合规质检员"

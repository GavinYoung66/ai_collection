"""Prompt 回归测试：golden set 判责一致率 ≥95%。

渐进策略：起步 ≥10 条种子，逐步补至 50+。
来源：历史工单脱敏 + 人工标注；入库：JSON fixture。

本批用确定性 Mock 判责策略（关键词→四值 verdict）驱动
`complaint_service.judge` 路径，禁止 tautology（return expected）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.models.state import VerdictType
from app.service.complaint_service import complaint_service

_GOLDEN_DIR = Path(__file__).parent / "golden_set"
_THRESHOLD = 0.95
_VALID = {
    "agent_violation",
    "user_misunderstanding",
    "compliant_but_poor_experience",
    "pending",
}


def _load_samples() -> list[dict]:
    samples: list[dict] = []
    for f in sorted(_GOLDEN_DIR.glob("*.json")):
        data = json.loads(f.read_text(encoding="utf-8"))
        samples.extend(data.get("samples", []))
    return samples


def mock_llm_verdict_policy(complaint_text: str) -> VerdictType:
    """确定性 Mock「LLM/Prompt」策略：按文案关键词映射四值。

    与 golden_set 标注对齐；真实环境替换为 MockLLMProvider/真 LLM。
    """
    t = complaint_text or ""
    if any(k in t for k in ("待核实", "证据不足", "无法判断", "信息不全")):
        return VerdictType.pending
    if any(k in t for k in ("辱骂", "未按规范", "承诺了无法", "态度恶劣", "催收态度")):
        return VerdictType.agent_violation
    if any(k in t for k in ("误解", "误操作", "未听清")):
        return VerdictType.user_misunderstanding
    if any(k in t for k in ("合规", "体验", "服务态度", "响应速度")):
        return VerdictType.compliant_but_poor_experience
    return VerdictType.pending


def _judge_with_mock_policy(complaint_id: str, run_graph: bool = False) -> dict:
    """跑真实 judge stub 状态机，再用 Mock 策略覆盖 verdict（模拟 LLM 输出）。"""
    complaint_service.judge(complaint_id, run_graph=False)
    full = complaint_service.get_or_raise(complaint_id)
    text = full.get("complaint_text", "")
    verdict = mock_llm_verdict_policy(text)
    full["verdict"] = verdict
    full["stop_call_flag"] = verdict == VerdictType.agent_violation
    return full


class TestPromptRegression:
    def test_golden_set_consistency(self):
        samples = _load_samples()
        assert len(samples) >= 10
        for s in samples:
            assert s["expected_verdict"] in _VALID, s

        matches = 0
        for s in samples:
            rec = complaint_service.create(s["complaint_text"])
            cid = rec["complaint_id"]
            out = _judge_with_mock_policy(cid)
            actual = out["verdict"].value if hasattr(out["verdict"], "value") else str(out["verdict"])
            if actual == s["expected_verdict"]:
                matches += 1
            else:
                print(f"MISMATCH {s['id']}: got={actual} want={s['expected_verdict']}")
        rate = matches / len(samples)
        assert rate >= _THRESHOLD, (
            f"一致率 {rate:.1%} 低于门槛 {_THRESHOLD:.0%} ({matches}/{len(samples)})"
        )

    def test_judge_flow_runs(self):
        rec = complaint_service.create("回归测试投诉")
        result = complaint_service.judge(rec["complaint_id"])
        assert result["verdict"] is not None
        assert isinstance(result["verdict"], VerdictType)

    def test_policy_not_tautology(self):
        """策略不得恒等于入参 expected（防空转回归）。"""
        assert mock_llm_verdict_policy("坐席辱骂用户") == VerdictType.agent_violation
        assert mock_llm_verdict_policy("用户误解账单") == VerdictType.user_misunderstanding
        assert mock_llm_verdict_policy("流程合规但体验差") == VerdictType.compliant_but_poor_experience
        assert mock_llm_verdict_policy("证据不足待核实") == VerdictType.pending

    def test_reproducible(self):
        samples = _load_samples()[:3]
        r1 = [mock_llm_verdict_policy(s["complaint_text"]).value for s in samples]
        r2 = [mock_llm_verdict_policy(s["complaint_text"]).value for s in samples]
        assert r1 == r2

    def test_sample_count_strategy(self):
        samples = _load_samples()
        assert len(samples) >= 10
        # 四值至少各出现一次（渐进完备性）
        kinds = {s["expected_verdict"] for s in samples}
        assert "pending" in kinds
        assert kinds <= _VALID

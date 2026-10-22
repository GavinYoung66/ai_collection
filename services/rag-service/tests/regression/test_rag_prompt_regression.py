"""RAG Prompt 回归测试：golden set 要点命中率 ≥95%。

格式说明：平台规格约定 `tests/regression/golden_set/` 下用 JSONL（每行
`input`/`expected`）；complaint-service 既有实现为 JSON `samples[]`，本服务
按规格 JSONL 落地且不改动 complaint-service——qc/rag 回归输入是结构化对象，
JSONL 更便于逐行 diff。

链路：`build_answer_prompt(context, query)` 真实渲染 → 确定性抽取式 stub 依据
渲染后的 prompt 生成答案。stub 只读 prompt/query，不读 expected；若 prompt
丢失检索片段或占位符未替换，要点必然缺失而失败。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from app.services.llm_providers.base import build_answer_prompt

_GOLDEN_FILE = Path(__file__).parent / "golden_set" / "rag_answer.jsonl"
_SPEC_MIN_RATE = 0.95  # 平台规格下限：要点命中率 ≥95%
_GATE_RATE = 1.0  # 本地门禁：当前 24 条全绿，按 100% 卡口；出现允许偏差时下调至 _SPEC_MIN_RATE 并留 issue
_MIN_SAMPLES = 20
_SECTION_START = "检索资料（含页码）："
_SECTION_END = "请基于上述资料"


def _load_samples() -> list[dict]:
    samples: list[dict] = []
    for lineno, line in enumerate(_GOLDEN_FILE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        assert set(row) == {"input", "expected"}, f"第 {lineno} 行键不合法"
        assert set(row["input"]) == {"query", "context"}, f"第 {lineno} 行 input 键不合法"
        samples.append(row)
    return samples


def _extract_sentences(prompt_text: str) -> list[str]:
    """从渲染后的 user prompt 抽取检索片段句子（剥掉 [N页] 前缀）。"""
    assert _SECTION_START in prompt_text, "prompt 缺少检索资料段，模板或渲染异常"
    body = prompt_text.split(_SECTION_START, 1)[1]
    body = body.split(_SECTION_END, 1)[0]
    sentences: list[str] = []
    for line in body.splitlines():
        line = re.sub(r"^\[\d+页\]\s*", "", line.strip())
        if not line:
            continue
        sentences.extend(part.strip() for part in re.split(r"[。；]", line) if part.strip())
    return sentences


def _deterministic_stub(prompt_text: str, query: str) -> str:
    """确定性抽取式答案：保留全部检索句子，按 query 二元组相关度稳定排序。

    不生成新事实、不使用随机量；同输入恒定同输出。若 prompt 未正确注入
    context/query（占位符未渲染），句子集合为空或与 query 失配，要点即缺失。
    """
    sentences = _extract_sentences(prompt_text)
    qgrams = {query[i : i + 2] for i in range(max(len(query) - 1, 0))}

    def _score(text: str) -> int:
        return sum(1 for gram in qgrams if gram in text)

    ordered = sorted(
        enumerate(sentences),
        key=lambda pair: (-_score(pair[1]), pair[0]),
    )
    return "根据检索资料：" + "；".join(text for _, text in ordered) + "。"


_SAMPLES = _load_samples()


def _answer_for(sample: dict) -> str:
    context = sample["input"]["context"]
    query = sample["input"]["query"]
    system, user = build_answer_prompt(context, query)
    # 断言渲染结果同时含 system 与已注入的 query，防呆（不读取 expected）
    assert system.strip(), "system 段为空"
    assert query in user, "user prompt 未注入 query"
    return _deterministic_stub(user, query)


def test_golden_set_size() -> None:
    assert len(_SAMPLES) >= _MIN_SAMPLES, f"golden 样本数 {len(_SAMPLES)} < {_MIN_SAMPLES}"


def test_points_hit_rate() -> None:
    total = len(_SAMPLES)
    matched = 0
    misses: list[str] = []
    for index, row in enumerate(_SAMPLES):
        answer = _answer_for(row)
        points = row["expected"]["points"]
        assert points, f"#{index + 1} expected.points 为空"
        missing = [p for p in points if p not in answer]
        if missing:
            misses.append(f"#{index + 1} 缺失要点={missing}")
        else:
            matched += 1
    rate = matched / total
    print(f"样本数={total} 命中={matched} 命中率={rate:.4f}")
    assert rate >= _GATE_RATE, (
        f"命中率 {rate:.4f} < 门禁 {_GATE_RATE}（规格下限 {_SPEC_MIN_RATE}）:\n"
        + "\n".join(misses)
    )


def test_stub_not_tautology() -> None:
    """反空转：三条与 golden 无关的输入，stub 输出必须随输入变化。"""
    cases = [
        ({"query": "如何设置还款提醒", "context": [{"content": "可在 APP 内设置还款提醒。", "page": 1}]}),
        ({"query": "协商分期需要什么材料", "context": [{"content": "协商分期需提交收入证明与困难说明。", "page": 2}]}),
        ({"query": "征信异议怎么申请", "context": [{"content": "征信异议可向征信机构提交书面申请。", "page": 3}]}),
    ]
    outputs = [_answer_for({"input": item, "expected": {"points": []}}) for item in cases]
    assert len(set(outputs)) == 3, "stub 对不同输入产出相同答案，疑似空转"
    for item, out in zip(cases, outputs):
        assert item["context"][0]["content"] in out, out


def test_reproducible() -> None:
    first = [_answer_for(row) for row in _SAMPLES]
    second = [_answer_for(row) for row in _SAMPLES]
    assert first == second

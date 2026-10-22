"""QC Prompt 回归测试：golden set 规则命中集合一致率 ≥95%。

格式差异说明：平台规格约定 `tests/regression/golden_set/` 下用 JSONL（每行
`input`/`expected`），而 complaint-service 既有实现为 JSON `samples[]` +
`complaint_text`/`expected_verdict`。本服务按规格 JSONL 落地，不改动
complaint-service；差异原因：qc 回归输入是结构化会话（turns + 会话字段），
JSONL 便于逐行样本 diff 与增量追加。

驱动方式：确定性策略 `_deterministic_policy` 直接复用 `app/services/rule_engine.py`
的关键词/字段判定词表（R14 词表来自 `app/seeds/qc_rules_v1.py`），不在测试内
复制常量，避免规则改词后回归静默漂移。策略禁止读取 expected 字段，禁止
return expected 式 tautology。

Prompt 契约：`test_prompt_*` 三条断言把 qc_review 模板与 golden/规则包绑在一起——
模板删段、删输出字段、引用不存在的规则或丢掉 redline_tag 枚举，回归即失败，
对应规格「prompt 变更导致一致率低于阈值则门禁拦截」在 qc 侧的可执行路径。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Iterable

from app.seeds.qc_rules_v1 import R14_KEYWORDS, V1_SEED
from app.services import rule_engine
from common.prompt.loader import load_prompt_sections

_GOLDEN_FILE = Path(__file__).parent / "golden_set" / "qc_review.jsonl"
_SPEC_MIN_RATE = 0.95  # 平台规格下限：golden 一致率 ≥95%
_GATE_RATE = 1.0  # 本地门禁：当前 24 条全绿，按 100% 卡口；出现允许偏差时下调至 _SPEC_MIN_RATE 并留 issue
_MIN_SAMPLES = 20

# 关键词表唯一来源：rule_engine / qc_rules_v1，此处不做本地复制。
_RELATION_WORDS = rule_engine._RELATION_WORDS
_COMMIT_WORDS = rule_engine._COMMIT_WORDS
_DISCLOSURE_WORDS = rule_engine._DISCLOSURE_WORDS
_CONFIRM_PHRASES = rule_engine._CONFIRM_PHRASES
_ID_ASK_PREFIX = rule_engine._ID_ASK_PREFIX
_ID_ASK_TITLE = rule_engine._ID_ASK_TITLE
_ID_NEXT_YES = rule_engine._ID_NEXT_YES
_ID_NEXT_NO = rule_engine._ID_NEXT_NO
_R12_PHRASES = rule_engine._R12_PHRASES
_R11_PHRASES = rule_engine._R11_PHRASES
_R13_USER = rule_engine._R13_USER
_R13_AGENT_PRESSURE = rule_engine._R13_AGENT_PRESSURE
_SILENCE = rule_engine._SILENCE
_R14_KEYWORDS = R14_KEYWORDS

_REQUIRED_SECTIONS = {
    "role",
    "task",
    "input_constraint",
    "output_schema",
    "prohibitions",
    "fallback",
    "enum_constraints",
    "few_shot",
}
_REQUIRED_OUTPUT_FIELDS = {"level", "rule_id", "evidence", "reason", "confidence"}
_RULE_ID_PATTERN = re.compile(r"R\d{2}")


def _contains(text: str, needles: Iterable[str]) -> str | None:
    blob = (text or "").casefold()
    for item in needles:
        token = item.strip()
        if token and token.casefold() in blob:
            return token
    return None


def _is_id_question(text: str) -> bool:
    if not _contains(text, _ID_ASK_PREFIX):
        return False
    if not _contains(text, _ID_ASK_TITLE):
        return False
    return _contains(text, _DISCLOSURE_WORDS) is None


def _is_short_yes(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 8:
        return False
    if _contains(stripped, _ID_NEXT_NO):
        return False
    return _contains(stripped, _ID_NEXT_YES) is not None


def _deterministic_policy(session: dict) -> set[str]:
    """确定性「LLM/Prompt」替身：按会话字段与关键词产出规则命中集合。

    不读取 expected，不使用随机量；同一输入恒定同输出。
    覆盖 R01/R02/R03/R04/R05/R06/R09/R11/R12/R13/R14（R07/R08/R10/R15 需
    快照或时长阈值等扩展字段，本批样本不涉及）。
    """
    turns = sorted(
        session.get("turns") or [],
        key=lambda t: (t.get("turn_index") or 0, t.get("turn_id") or ""),
    )
    agent_turns = [t for t in turns if t.get("speaker") == "agent"]
    rules: set[str] = set()

    started_at = str(session.get("started_at") or "")
    if started_at:
        hour = int(started_at[11:13])
        if hour < 8 or hour >= 21:
            rules.add("R02")

    duration = session.get("duration_seconds")
    if duration is not None and int(duration) < 10:
        rules.add("R09")

    dial_count = session.get("dial_count_24h")
    if dial_count is not None and int(dial_count) > 3:
        rules.add("R05")

    streak = 0
    for turn in turns:
        if (turn.get("text") or "").strip() in _SILENCE:
            streak += 1
            if streak >= 3:
                rules.add("R06")
        else:
            streak = 0

    for turn in agent_turns:
        text = turn.get("text") or ""
        if _contains(text, _R12_PHRASES):
            rules.add("R12")
        if _contains(text, _R11_PHRASES):
            rules.add("R11")
        if _contains(text, _R14_KEYWORDS):
            rules.add("R14")
        if _contains(text, _RELATION_WORDS):
            rules.add("R01")
        if _contains(text, _COMMIT_WORDS):
            rules.add("R03")

    confirmed = False
    triggered = False
    for index, turn in enumerate(turns):
        text = turn.get("text") or ""
        speaker = turn.get("speaker")
        if speaker == "user":
            if _contains(text, _CONFIRM_PHRASES):
                confirmed = True
            if _contains(text, _R13_USER):
                triggered = True
                rules.add("R13")
            continue
        if speaker != "agent":
            continue
        if _is_id_question(text):
            nxt = turns[index + 1] if index + 1 < len(turns) else None
            if nxt and nxt.get("speaker") == "user" and _is_short_yes(nxt.get("text") or ""):
                confirmed = True
            continue
        if not confirmed and _contains(text, _DISCLOSURE_WORDS):
            rules.add("R04")
        if triggered and _contains(text, _R13_AGENT_PRESSURE):
            rules.add("R13")
    return rules


def _load_samples() -> list[dict]:
    samples: list[dict] = []
    for lineno, line in enumerate(_GOLDEN_FILE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        assert set(row) == {"input", "expected"}, f"第 {lineno} 行键不合法"
        samples.append(row)
    return samples


_SAMPLES = _load_samples()


def test_golden_set_size() -> None:
    assert len(_SAMPLES) >= _MIN_SAMPLES, f"golden 样本数 {len(_SAMPLES)} < {_MIN_SAMPLES}"


def test_policy_agreement_rate() -> None:
    total = len(_SAMPLES)
    matched = 0
    misses: list[str] = []
    for index, row in enumerate(_SAMPLES):
        got = _deterministic_policy(row["input"])
        expected = set(row["expected"])
        if got == expected:
            matched += 1
        else:
            misses.append(f"#{index + 1} expected={sorted(expected)} got={sorted(got)}")
    rate = matched / total
    print(f"样本数={total} 一致={matched} 一致率={rate:.4f}")
    assert rate >= _GATE_RATE, (
        f"一致率 {rate:.4f} < 门禁 {_GATE_RATE}（规格下限 {_SPEC_MIN_RATE}）:\n"
        + "\n".join(misses)
    )


def test_policy_not_tautology() -> None:
    """反空转：与 golden 无关的三条输入，策略输出必须随之变化。"""
    clean = {
        "turns": [{"turn_id": "a", "speaker": "agent", "text": "请通过官方入口还款。", "turn_index": 1}],
        "duration_seconds": 300,
        "dial_count_24h": 1,
        "started_at": "2026-09-09T10:00:00+08:00",
    }
    impersonation = {
        "turns": [{"turn_id": "a", "speaker": "agent", "text": "我是法官。", "turn_index": 1}],
        "duration_seconds": 300,
        "dial_count_24h": 1,
        "started_at": "2026-09-09T10:00:00+08:00",
    }
    overstate = {
        "turns": [{"turn_id": "a", "speaker": "agent", "text": "马上立案抓人。", "turn_index": 1}],
        "duration_seconds": 300,
        "dial_count_24h": 1,
        "started_at": "2026-09-09T10:00:00+08:00",
    }
    outputs = [
        frozenset(_deterministic_policy(item))
        for item in (clean, impersonation, overstate)
    ]
    assert outputs[0] == frozenset(), outputs[0]
    assert outputs[1] == {"R12"}, outputs[1]
    assert outputs[2] == {"R14"}, outputs[2]
    assert len(set(outputs)) == 3, "策略对不同输入产出相同集合，疑似空转"


def test_reproducible() -> None:
    first = [_deterministic_policy(row["input"]) for row in _SAMPLES]
    second = [_deterministic_policy(row["input"]) for row in _SAMPLES]
    assert first == second


def _active_rules() -> dict[str, dict]:
    return {
        str(rule["rule_id"]): rule
        for rule in V1_SEED["rules"]
        if rule.get("enabled") is not False
    }


def test_prompt_sections_intact() -> None:
    """prompt 契约：standard 七段齐全，删段即失败（防 simple 分流被绕过）。"""
    sections = load_prompt_sections("qc_review", service="qc-service")["sections"]
    missing = _REQUIRED_SECTIONS - set(sections)
    assert not missing, f"prompt 缺失段: {sorted(missing)}"


def test_prompt_output_schema_matches_consumers() -> None:
    """prompt 契约：质检结果解析依赖的输出字段必须仍在 properties 与 required 中。"""
    schema = load_prompt_sections("qc_review", service="qc-service")["sections"][
        "output_schema"
    ]["items"]
    props = set(schema["properties"])
    required = set(schema["required"])
    assert _REQUIRED_OUTPUT_FIELDS <= props, f"output_schema 缺字段: {sorted(_REQUIRED_OUTPUT_FIELDS - props)}"
    assert _REQUIRED_OUTPUT_FIELDS <= required, f"required 缺字段: {sorted(_REQUIRED_OUTPUT_FIELDS - required)}"
    assert schema["properties"]["level"]["enum"] == ["high", "medium", "low"]


def test_prompt_rule_vocabulary_consistent() -> None:
    """prompt × golden × 规则包三方一致（契约断言，允许读 expected）。

    - prompt 提及的 rule_id 必须是启用规则，否则模板写错规则号即失败；
    - golden 使用的 rule_id 必须是启用规则，规则被禁用/删除即失败；
    - golden 规则的 redline_tag 必须在 prompt 的 redline_tag 枚举内，
      模板丢掉某个标签即失败。
    """
    sections = load_prompt_sections("qc_review", service="qc-service")["sections"]
    active = _active_rules()
    schema = sections["output_schema"]["items"]["properties"]
    tag_enum = set(schema["redline_tag"]["enum"])
    mentioned = set(_RULE_ID_PATTERN.findall(json.dumps(sections, ensure_ascii=False)))
    assert mentioned <= set(active), f"prompt 引用了不存在的规则: {sorted(mentioned - set(active))}"
    used = {rid for row in _SAMPLES for rid in row["expected"]}
    assert used <= set(active), f"golden 使用了未启用规则: {sorted(used - set(active))}"
    for rid in sorted(used):
        tag = active[rid].get("redline_tag")
        if tag:
            assert tag in tag_enum, f"{rid} 的 redline_tag={tag} 不在 prompt 枚举内"

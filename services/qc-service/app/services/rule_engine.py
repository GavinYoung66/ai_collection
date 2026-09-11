"""业务规则引擎：R01–R09、R11–R15。本轮不求值 R10。"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence
from zoneinfo import ZoneInfo

from app.integrations.qc_session import QCSession, QcTurn, map_speaker
from app.seeds.qc_rules_v1 import V1_SEED
from app.services.ac_filter import WordHit

_SHANGHAI = ZoneInfo("Asia/Shanghai")
RULES_IN_ROUND = (
    "R01",
    "R02",
    "R03",
    "R04",
    "R05",
    "R06",
    "R07",
    "R08",
    "R09",
    "R11",
    "R12",
    "R13",
    "R14",
    "R15",
)

_RELATION_WORDS = (
    "妈妈",
    "爸爸",
    "母亲",
    "父亲",
    "家属",
    "家人",
    "老公",
    "老婆",
    "丈夫",
    "妻子",
    "儿子",
    "女儿",
    "同事",
    "领导",
    "朋友",
    "亲戚",
)
_COMMIT_WORDS = ("减免", "延期", "免息", "分期")
_DISCLOSURE_WORDS = ("欠款", "逾期", "欠了", "要还", "应还", "未还", "账单", "欠费")
_CONFIRM_PHRASES = ("是我", "我是本人", "对是我", "嗯是我", "我就是", "没错是我", "对的是我")
_ID_ASK_PREFIX = ("您是", "请问是")
_ID_ASK_TITLE = ("先生", "女士", "本人")
_ID_NEXT_YES = ("是", "对", "嗯", "没错")
_ID_NEXT_NO = ("不是", "不对")
_R12_PHRASES = (
    "我是法院",
    "我是公安",
    "我是警察",
    "我是律师",
    "我是法官",
    "我是检察院",
    "官方机构",
    "催收专员",
)
_R11_PHRASES = ("必须还", "不能不还", "现在就还", "今天必须还", "没有拒绝的权利", "不想还也不行")
_R13_USER = ("不要再打", "别打了", "停止致电", "不要打电话", "投诉", "自杀", "不想活", "拒绝沟通")
_R13_AGENT_PRESSURE = ("还钱", "必须还", "欠款", "逾期", "不还就", "今天还")
_SILENCE = ("", "无应答", "沉默", "……", "...")
_AMOUNT_UNIT = ("元", "块", "金额")


@dataclass
class RuleEngineResult:
    hits: list[WordHit] = field(default_factory=list)
    skipped_rules: list[str] = field(default_factory=list)


def _contains(text: str, needles: Iterable[str]) -> str | None:
    blob = text or ""
    lowered = blob.casefold()
    for item in needles:
        token = item.strip()
        if token and token.casefold() in lowered:
            return token
    return None


def _field(session: Any, name: str, default: Any = None) -> Any:
    if isinstance(session, Mapping):
        return session.get(name, default)
    return getattr(session, name, default)


def _turns(session: Any) -> list[dict[str, Any]]:
    raw = _field(session, "turns") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, QcTurn):
            out.append(
                {
                    "turn_id": item.turn_id,
                    "speaker": map_speaker(item.speaker),
                    "text": item.text or "",
                    "turn_index": item.turn_index,
                    "hit_ts": item.hit_ts,
                    "start_offset_ms": item.start_offset_ms,
                }
            )
        elif isinstance(item, Mapping):
            out.append(
                {
                    "turn_id": item.get("turn_id"),
                    "speaker": map_speaker(item.get("speaker")),
                    "text": item.get("text") or "",
                    "turn_index": item.get("turn_index") or 0,
                    "hit_ts": item.get("hit_ts"),
                    "start_offset_ms": item.get("start_offset_ms"),
                }
            )
    return sorted(out, key=lambda row: (row["turn_index"], row["turn_id"] or ""))


def _snapshot(session: Any) -> Mapping[str, Any] | None:
    snap = _field(session, "case_snapshot")
    if snap is None:
        return None
    if hasattr(snap, "model_dump"):
        return snap.model_dump()
    if isinstance(snap, Mapping):
        return snap
    return None


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=_SHANGHAI)
    return dt.astimezone(_SHANGHAI)


def _rule_hit(
    rule_id: str,
    *,
    speaker: str,
    text: str,
    turn: Mapping[str, Any] | None,
    severity: str,
    redline_tag: str | None,
    word: str | None = None,
    score_eligible: bool | None = None,
) -> WordHit:
    mapped = map_speaker(speaker)
    eligible = mapped == "agent" if score_eligible is None else score_eligible
    return WordHit(
        speaker=mapped,
        word=word or rule_id,
        severity=severity,  # type: ignore[arg-type]
        score_eligible=eligible,
        source="rule",
        redline_tag=redline_tag,
        rule_id=rule_id,
        evidence_text=text,
        turn_id=None if turn is None else turn.get("turn_id"),
        turn_index=None if turn is None else turn.get("turn_index"),
        hit_ts=None if turn is None else turn.get("hit_ts"),
        start_offset_ms=None if turn is None else turn.get("start_offset_ms"),
    )


def _meta_from_pack(pack: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in pack.get("rules") or []:
        rid = item.get("rule_id")
        if rid:
            out[str(rid)] = item
    return out


def _r14_keywords(pack: Mapping[str, Any]) -> list[str]:
    item = _meta_from_pack(pack).get("R14") or {}
    params = item.get("params") or {}
    words = params.get("keywords") or []
    return [str(w).strip() for w in words if str(w).strip()]


def _identity_confirmed_after(turns: Sequence[Mapping[str, Any]], *, upto_index: int | None = None) -> bool:
    confirmed = False
    ordered = list(turns)
    for i, turn in enumerate(ordered):
        if upto_index is not None and turn["turn_index"] > upto_index:
            break
        text = turn["text"]
        speaker = turn["speaker"]
        if speaker == "user" and _contains(text, _CONFIRM_PHRASES):
            confirmed = True
        if speaker == "agent" and _is_id_question(text):
            nxt = ordered[i + 1] if i + 1 < len(ordered) else None
            if nxt and nxt["speaker"] == "user" and _is_short_yes(nxt["text"]):
                confirmed = True
    return confirmed


def _is_id_question(text: str) -> bool:
    if not _contains(text, _ID_ASK_PREFIX):
        return False
    if not _contains(text, _ID_ASK_TITLE):
        return False
    return _contains(text, _DISCLOSURE_WORDS) is None


def _is_short_yes(text: str) -> bool:
    """§5.2.4 核身下一轮肯定：是/对/嗯/没错，长度 ≤8；「不是」「不对」不算肯定。"""
    stripped = (text or "").strip()
    if not stripped or len(stripped) > 8:
        return False
    if _contains(stripped, _ID_NEXT_NO):
        return False
    return _contains(stripped, _ID_NEXT_YES) is not None


class RuleEngine:
    """R01–R09、R11–R15。skip 不产生 hit、不 PARTIAL、不宣称红线已通过。"""

    def __init__(self, rules: Mapping[str, Any] | None = None) -> None:
        self.rules = dict(rules or V1_SEED)

    def run(self, session: QCSession | Mapping[str, Any]) -> RuleEngineResult:
        hits: list[WordHit] = []
        skipped: list[str] = []
        meta = _meta_from_pack(self.rules)
        turns = _turns(session)
        dispatch = {
            "R01": self._r01,
            "R02": self._r02,
            "R03": self._r03,
            "R04": self._r04,
            "R05": self._r05,
            "R06": self._r06,
            "R07": self._r07,
            "R08": self._r08,
            "R09": self._r09,
            "R11": self._r11,
            "R12": self._r12,
            "R13": self._r13,
            "R14": self._r14,
            "R15": self._r15,
        }
        for rule_id in RULES_IN_ROUND:
            spec = meta.get(rule_id) or {}
            if spec.get("enabled") is False:
                continue
            produced, was_skipped = dispatch[rule_id](session, turns, spec)
            if was_skipped:
                skipped.append(rule_id)
                continue
            hits.extend(produced)
        return RuleEngineResult(hits=hits, skipped_rules=skipped)

    def _r01(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        name = _field(session, "debtor_name")
        if name is None or str(name).strip() == "":
            return [], True
        hits: list[WordHit] = []
        for turn in turns:
            if turn["speaker"] != "agent":
                continue
            word = _contains(turn["text"], _RELATION_WORDS)
            if word:
                hits.append(self._hit("R01", spec, turn, word=word))
        return hits, False

    def _r02(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        started = _parse_dt(_field(session, "started_at"))
        ended = _parse_dt(_field(session, "ended_at"))
        if started is None and ended is None:
            return [], True
        points = [item for item in (started, ended) if item is not None]
        if any(dt.hour < 8 or dt.hour >= 21 for dt in points):
            turn = turns[0] if turns else None
            return [self._hit("R02", spec, turn, speaker="agent", text="外呼时段违规")], False
        return [], False

    def _r03(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        snap = _snapshot(session)
        if snap is None or "strategy_refs" not in snap:
            return [], True
        refs = [str(item) for item in (snap.get("strategy_refs") or []) if item]
        hits: list[WordHit] = []
        for turn in turns:
            if turn["speaker"] != "agent":
                continue
            word = _contains(turn["text"], _COMMIT_WORDS)
            if not word:
                continue
            cited = any(ref and ref in turn["text"] for ref in refs)
            if not cited:
                hits.append(self._hit("R03", spec, turn, word=word))
        return hits, False

    def _r04(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        confirmed = False
        hits: list[WordHit] = []
        for i, turn in enumerate(turns):
            text = turn["text"]
            speaker = turn["speaker"]
            if speaker == "user" and _contains(text, _CONFIRM_PHRASES):
                confirmed = True
            if speaker == "agent" and _is_id_question(text):
                nxt = turns[i + 1] if i + 1 < len(turns) else None
                if nxt and nxt["speaker"] == "user" and _is_short_yes(nxt["text"]):
                    confirmed = True
                continue
            if speaker == "agent" and not confirmed:
                word = _contains(text, _DISCLOSURE_WORDS)
                if word:
                    hits.append(self._hit("R04", spec, turn, word=word))
        return hits, False

    def _r05(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        hist = _field(session, "dial_history_24h")
        if hist is None:
            return [], True
        data = hist.model_dump() if hasattr(hist, "model_dump") else dict(hist)
        if "dial_count_24h" not in data or data.get("dial_count_24h") is None:
            return [], True
        count = int(data["dial_count_24h"])
        if count > 3:
            turn = turns[0] if turns else None
            return [self._hit("R05", spec, turn, speaker="agent", text=f"dial_count_24h={count}")], False
        return [], False

    def _r06(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        streak = 0
        last: dict[str, Any] | None = None
        for turn in turns:
            silent = turn["text"].strip() in _SILENCE
            if silent:
                streak += 1
                last = turn
                if streak >= 3 and last is not None:
                    return [self._hit("R06", spec, last, speaker="agent", text=last["text"])], False
            else:
                streak = 0
        return [], False

    def _r07(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        agent_type = _field(session, "agent_type")
        if agent_type != "ai":
            return [], True
        if not turns or max(t["turn_index"] for t in turns) < 13:
            return [], False
        if _identity_confirmed_after(turns, upto_index=13):
            return [], False
        last = next((t for t in turns if t["turn_index"] == 13), turns[-1])
        return [self._hit("R07", spec, last, speaker="agent", text=last["text"])], False

    def _r08(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        snap = _snapshot(session) or {}
        due = snap.get("due_amount")
        ptp = snap.get("ptp_amount")
        if due is None or ptp is None:
            return [], True
        due_f = float(due)
        if due_f == 0:
            return [], True
        if abs(float(ptp) - due_f) / due_f > 0.20:
            turn = turns[0] if turns else None
            return [self._hit("R08", spec, turn, speaker="agent", text=str(ptp))], False
        return [], False

    def _r09(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        seconds = _field(session, "duration_seconds")
        millis = _field(session, "duration_ms")
        if seconds is None and millis is None:
            return [], True
        duration = int(seconds) if seconds is not None else int(millis) // 1000
        if duration < 10:
            turn = turns[0] if turns else None
            return [self._hit("R09", spec, turn, speaker="agent", text=str(duration))], False
        return [], False

    def _r11(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        hits: list[WordHit] = []
        for turn in turns:
            if turn["speaker"] != "agent":
                continue
            word = _contains(turn["text"], _R11_PHRASES)
            if word:
                hits.append(self._hit("R11", spec, turn, word=word))
        return hits, False

    def _r12(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        hits: list[WordHit] = []
        for turn in turns:
            if turn["speaker"] != "agent":
                continue
            word = _contains(turn["text"], _R12_PHRASES)
            if word:
                hits.append(self._hit("R12", spec, turn, word=word))
        return hits, False

    def _r13(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        hits: list[WordHit] = []
        triggered = False
        for turn in turns:
            if turn["speaker"] == "user":
                word = _contains(turn["text"], _R13_USER)
                if word:
                    triggered = True
                    hits.append(
                        self._hit(
                            "R13",
                            spec,
                            turn,
                            word=word,
                            score_eligible=False,
                        )
                    )
                continue
            if triggered and turn["speaker"] == "agent":
                word = _contains(turn["text"], _R13_AGENT_PRESSURE)
                if word:
                    hits.append(self._hit("R13", spec, turn, word=word, score_eligible=True))
        return hits, False

    def _r14(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        keywords = _r14_keywords(self.rules)
        snap = _snapshot(session) or {}
        raw_hit = snap.get("standard_answer_hit")
        hit_text = raw_hit.strip() if isinstance(raw_hit, str) and raw_hit.strip() else None
        if not keywords and not hit_text:
            return [], True
        agent_turns = [t for t in turns if t["speaker"] == "agent"]
        agent_blob = "".join(t["text"] for t in agent_turns)
        hits: list[WordHit] = []
        if not hit_text:
            for turn in agent_turns:
                word = _contains(turn["text"], keywords)
                if word:
                    hits.append(self._hit("R14", spec, turn, word=word))
            return hits, False
        if hit_text.casefold() in agent_blob.casefold():
            return [], False
        if keywords:
            for turn in agent_turns:
                word = _contains(turn["text"], keywords)
                if word:
                    hits.append(self._hit("R14", spec, turn, word=word))
            return hits, False
        turn = agent_turns[0] if agent_turns else None
        return [self._hit("R14", spec, turn, speaker="agent", text=agent_blob, word="standard_answer_miss")], False

    def _r15(self, session: Any, turns: list[dict[str, Any]], spec: Mapping[str, Any]) -> tuple[list[WordHit], bool]:
        snap = _snapshot(session) or {}
        due = snap.get("due_amount")
        days = snap.get("overdue_days")
        if due is None and days is None:
            return [], True
        hits: list[WordHit] = []
        due_norm = None if due is None else str(int(float(due)))
        days_norm = None if days is None else str(int(days))
        for turn in turns:
            if turn["speaker"] != "agent":
                continue
            text = turn["text"]
            numbers = re.findall(r"\d+", text)
            if due_norm and _contains(text, _AMOUNT_UNIT):
                if any(num != due_norm and num != days_norm for num in numbers):
                    hits.append(self._hit("R15", spec, turn, word=numbers[0]))
                    continue
            if days_norm and "天" in text:
                if any(num != days_norm and num != due_norm for num in numbers):
                    hits.append(self._hit("R15", spec, turn, word=numbers[0]))
        return hits, False

    def _hit(
        self,
        rule_id: str,
        spec: Mapping[str, Any],
        turn: Mapping[str, Any] | None,
        *,
        word: str | None = None,
        speaker: str | None = None,
        text: str | None = None,
        score_eligible: bool | None = None,
    ) -> WordHit:
        return _rule_hit(
            rule_id,
            speaker=speaker or (turn or {}).get("speaker") or "agent",
            text=text if text is not None else (turn or {}).get("text") or "",
            turn=turn,
            severity=str(spec.get("severity") or "medium"),
            redline_tag=spec.get("redline_tag"),
            word=word,
            score_eligible=score_eligible,
        )

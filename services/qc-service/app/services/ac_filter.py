"""AC 适配：逐 turn 敏感词匹配；仅 agent 计分。"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Sequence

from app.integrations.guardrails_adapter import classify_category, matcher_from_words
from app.integrations.qc_session import map_speaker
from common.security.sensitive_words import SensitiveWordMatcher

logger = logging.getLogger(__name__)

# pyahocorasick: empty trie / no make_automaton → AttributeError with this message.
_AUTOMATON_NOT_READY_MARKERS = ("make_automaton", "Aho-Corasick automaton")


@dataclass(frozen=True)
class SensitiveWord:
    word: str
    category: str = "induce"
    word_id: str | None = None


@dataclass
class WordHit:
    speaker: Literal["agent", "user"]
    word: str
    severity: Literal["high", "medium", "low"]
    score_eligible: bool
    source: str = "ac"
    word_id: str | None = None
    normalized_word: str = ""
    redline_tag: str | None = None
    turn_id: str | None = None
    turn_index: int | None = None
    hit_ts: Any = None
    start_offset_ms: int | None = None
    end_offset_ms: int | None = None
    evidence_text: str | None = None
    rule_id: str | None = None
    extras: dict[str, Any] = field(default_factory=dict)


def normalize_word(word: str) -> str:
    return (word or "").strip().casefold()


_shared_ac: ACFilter | None = None


def get_shared_ac() -> ACFilter:
    """进程内共享 AC，供 WordLoader 重建后被 OfflineQcService 复用。"""
    global _shared_ac
    if _shared_ac is None:
        _shared_ac = ACFilter()
    return _shared_ac


def reset_shared_ac() -> None:
    global _shared_ac
    _shared_ac = None


class ACFilter:
    """复用 SensitiveWordMatcher；user 行 score_eligible=false。"""

    def __init__(self) -> None:
        self._matcher = SensitiveWordMatcher()
        self._meta: dict[str, SensitiveWord] = {}

    async def reload(self, words: list[SensitiveWord]) -> None:
        self._meta = {item.word: item for item in words if item.word}
        self._matcher = matcher_from_words([item.word for item in words if item.word])

    def match(self, text: str, speaker: str) -> list[WordHit]:
        mapped = map_speaker(speaker)
        eligible = mapped == "agent"
        if not text:
            return []
        try:
            raw_hits = self._matcher.match(text)
        except AttributeError as exc:
            msg = str(exc)
            if any(marker in msg for marker in _AUTOMATON_NOT_READY_MARKERS):
                logger.warning("AC matcher automaton not ready: %s", exc)
                return []
            raise
        hits: list[WordHit] = []
        for raw in raw_hits:
            word = str(raw["word"])
            meta = self._meta.get(word)
            category = meta.category if meta else "induce"
            severity, redline = classify_category(category)
            hits.append(
                WordHit(
                    speaker=mapped,
                    word=word,
                    severity=severity,  # type: ignore[arg-type]
                    score_eligible=eligible,
                    source="ac",
                    word_id=meta.word_id if meta else None,
                    normalized_word=normalize_word(word),
                    redline_tag=redline,
                    start_offset_ms=None,
                    end_offset_ms=None,
                    evidence_text=text,
                )
            )
        return hits

    def match_turns(self, turns: Sequence[Any]) -> list[WordHit]:
        collected: list[WordHit] = []
        for turn in turns:
            if hasattr(turn, "text"):
                text = turn.text
                speaker = turn.speaker
                turn_id = getattr(turn, "turn_id", None)
                turn_index = getattr(turn, "turn_index", None)
                hit_ts = getattr(turn, "hit_ts", None)
                start_offset_ms = getattr(turn, "start_offset_ms", None)
            else:
                text = turn.get("text", "")
                speaker = turn.get("speaker", "agent")
                turn_id = turn.get("turn_id")
                turn_index = turn.get("turn_index")
                hit_ts = turn.get("hit_ts")
                start_offset_ms = turn.get("start_offset_ms")
            for hit in self.match(text, speaker):
                hit.turn_id = turn_id
                hit.turn_index = turn_index
                hit.hit_ts = hit_ts
                if start_offset_ms is not None:
                    hit.start_offset_ms = start_offset_ms
                collected.append(hit)
        return collected

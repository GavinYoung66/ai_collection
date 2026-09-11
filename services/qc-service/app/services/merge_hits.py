"""MergeHits：去重键、跨层同 tag、severity 最高；并列 AC > 规则 > LLM。"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from typing import Sequence

from app.services.ac_filter import WordHit, normalize_word

VALID_SPEAKERS = frozenset({"agent", "user"})
VALID_RULE_IDS = frozenset(f"R{i:02d}" for i in range(1, 16))
SEVERITY_RANK = {"high": 3, "medium": 2, "low": 1}
SOURCE_RANK = {"ac": 3, "rule": 2, "llm": 1}


def _source(hit: WordHit) -> str:
    src = (hit.source or "ac").lower()
    if src in SOURCE_RANK:
        return src
    if hit.rule_id:
        return "rule"
    return "ac"


def _ac_key(hit: WordHit) -> tuple:
    ident = hit.word_id or hit.normalized_word or normalize_word(hit.word)
    return ("ac", hit.speaker, ident, hit.turn_id)


def _rule_key(hit: WordHit) -> tuple:
    return ("rule", hit.speaker, hit.rule_id, hit.turn_id)


def _better(left: WordHit, right: WordHit) -> WordHit:
    ls, rs = SEVERITY_RANK.get(left.severity, 0), SEVERITY_RANK.get(right.severity, 0)
    if ls != rs:
        return left if ls > rs else right
    return left if SOURCE_RANK.get(_source(left), 0) >= SOURCE_RANK.get(_source(right), 0) else right


def _normalize(hit: WordHit) -> WordHit | None:
    if hit.speaker not in VALID_SPEAKERS:
        return None
    if _source(hit) == "llm" and hit.rule_id not in VALID_RULE_IDS:
        return None
    eligible = False if hit.speaker == "user" else hit.score_eligible
    return replace(hit, score_eligible=eligible)


def merge_hits(
    ac_hits: Sequence[WordHit] | None = None,
    rule_hits: Sequence[WordHit] | None = None,
    llm_hits: Sequence[WordHit] | None = None,
) -> list[WordHit]:
    """只对合并后列表告警。未合并重复不得进入结果。"""
    pooled: list[WordHit] = []
    for bucket, default_source in (
        (ac_hits or [], "ac"),
        (rule_hits or [], "rule"),
        (llm_hits or [], "llm"),
    ):
        for hit in bucket:
            src = hit.source or default_source
            normalized = _normalize(replace(hit, source=src))
            if normalized is not None:
                pooled.append(normalized)

    by_key: dict[tuple, WordHit] = {}
    order: list[tuple] = []
    for hit in pooled:
        key = _ac_key(hit) if _source(hit) == "ac" else _rule_key(hit)
        if key not in by_key:
            by_key[key] = hit
            order.append(key)
        else:
            by_key[key] = _better(by_key[key], hit)

    keyed = [by_key[key] for key in order]
    groups: dict[tuple, list[WordHit]] = defaultdict(list)
    untagged: list[WordHit] = []
    for hit in keyed:
        tag = hit.redline_tag or ""
        if tag:
            groups[(hit.speaker, hit.turn_id, tag)].append(hit)
        else:
            untagged.append(hit)

    merged: list[WordHit] = list(untagged)
    for group in groups.values():
        winner = group[0]
        for item in group[1:]:
            winner = _better(winner, item)
        rule_id = next((item.rule_id for item in group if item.rule_id), winner.rule_id)
        merged.append(replace(winner, rule_id=rule_id))
    return merged


class MergeHits:
    def apply(
        self,
        ac_hits: Sequence[WordHit] | None = None,
        rule_hits: Sequence[WordHit] | None = None,
        llm_hits: Sequence[WordHit] | None = None,
    ) -> list[WordHit]:
        return merge_hits(ac_hits, rule_hits, llm_hits)

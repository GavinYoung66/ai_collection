"""DedupACWindow：仅作用于 AC hit，W=same_word_window_ms，默认 30000ms。"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any, Sequence

from app.core.config import qc_settings
from app.services.ac_filter import WordHit, normalize_word

TURN_INDEX_WINDOW = 2
DEFAULT_SAME_WORD_WINDOW_MS = 30000


def _is_ac_hit(hit: WordHit) -> bool:
    if hit.source == "rule" or hit.source == "llm":
        return False
    if hit.rule_id and str(hit.rule_id).startswith("R"):
        return False
    return hit.source == "ac"


def _ts_ms(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return int(value.timestamp() * 1000)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp() * 1000)
    return None


def _dedup_key(hit: WordHit) -> tuple[str, str]:
    ident = hit.word_id or hit.normalized_word or normalize_word(hit.word)
    return (hit.speaker, ident)


def _sort_key(hit: WordHit) -> tuple[int, int]:
    ts = _ts_ms(hit.hit_ts)
    if ts is not None:
        return (0, ts)
    if hit.start_offset_ms is not None:
        return (1, hit.start_offset_ms)
    return (2, hit.turn_index if hit.turn_index is not None else 0)


def in_ac_window(first: WordHit, second: WordHit, window_ms: int) -> bool:
    """同一对选用最高可用精度，不混用。"""
    t1, t2 = _ts_ms(first.hit_ts), _ts_ms(second.hit_ts)
    if t1 is not None and t2 is not None:
        return abs(t1 - t2) < window_ms
    o1, o2 = first.start_offset_ms, second.start_offset_ms
    if o1 is not None and o2 is not None:
        return abs(o1 - o2) < window_ms
    i1, i2 = first.turn_index, second.turn_index
    if i1 is not None and i2 is not None:
        return abs(i1 - i2) <= TURN_INDEX_WINDOW
    return False


class DedupACWindow:
    def __init__(
        self,
        same_word_window_ms: int | None = None,
        *,
        window_ms: int | None = None,
    ) -> None:
        """W 来自 same_word_window_ms；window_ms 为同义入参，二者都传时以前者为准。

        未传入时默认 30000。QC_DEDUP_WINDOW_MS 是同默认的 env 别名；
        1.3 由 OfflineQcService 注入 active qc_rules.same_word_window_ms。
        """
        if same_word_window_ms is not None:
            resolved = same_word_window_ms
        elif window_ms is not None:
            resolved = window_ms
        else:
            resolved = qc_settings.QC_DEDUP_WINDOW_MS or DEFAULT_SAME_WORD_WINDOW_MS
        self.same_word_window_ms = int(resolved)
        self.window_ms = self.same_word_window_ms

    def apply(self, hits: Sequence[WordHit]) -> list[WordHit]:
        groups: dict[tuple[str, str], list[WordHit]] = defaultdict(list)
        for hit in hits:
            if _is_ac_hit(hit):
                groups[_dedup_key(hit)].append(hit)

        dropped: set[int] = set()
        for group in groups.values():
            ordered = sorted(group, key=_sort_key)
            last_kept: WordHit | None = None
            for hit in ordered:
                if last_kept is not None and in_ac_window(last_kept, hit, self.window_ms):
                    dropped.add(id(hit))
                    continue
                last_kept = hit

        return [hit for hit in hits if id(hit) not in dropped]

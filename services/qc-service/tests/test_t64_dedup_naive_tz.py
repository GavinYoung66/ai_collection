"""T64 / 6.3：dedup_ac naive 时间戳绑定 Asia/Shanghai 单测。

验收判据：无时区后缀的 hit_ts 与上海同时刻（aware）命中在同一 30s 窗口
去重成功——消除容器 UTC 下 AC 去重窗口静默误判 8 小时。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from app.services.ac_filter import WordHit
from app.services.dedup_ac import DedupACWindow, _ts_ms


def _hit(word: str, hit_ts) -> WordHit:
    return WordHit(
        speaker="agent",
        word=word,
        severity="high",
        score_eligible=True,
        source="ac",
        word_id=word,
        normalized_word=word,
        turn_index=0,
        hit_ts=hit_ts,
    )


class TestTsMsTimezone:
    def test_naive_string_bound_to_shanghai(self):
        """naive 串按 Asia/Shanghai 解释：10:00 naive = 02:00Z（而非本地 10:00Z）。"""
        naive_ms = _ts_ms("2026-11-05T10:00:00")
        aware_ms = _ts_ms("2026-11-05T10:00:00+08:00")
        assert naive_ms == aware_ms, (
            "naive 必须绑 Shanghai（= aware 上海同时刻），不得按进程本地时区"
        )

    def test_z_suffix_aware_unchanged(self):
        """Z 后缀（UTC aware）：原样解释。"""
        assert _ts_ms("2026-11-05T02:00:00Z") == _ts_ms("2026-11-05T10:00:00+08:00")

    def test_naive_datetime_bound_to_shanghai(self):
        """naive datetime 同样绑 Shanghai（与字符串分支同一语义）。"""
        from datetime import datetime

        naive = datetime(2026, 11, 5, 10, 0, 0)
        assert _ts_ms(naive) == _ts_ms("2026-11-05T10:00:00+08:00")

    def test_non_timezone_inputs_untouched(self):
        """None / 非法串 / 数值：原语义保留。"""
        assert _ts_ms(None) is None
        assert _ts_ms(1234) == 1234
        assert _ts_ms(1.5) == 1
        assert _ts_ms("") is None


class TestDedupWindowNaiveAwareness:
    def test_naive_and_aware_same_moment_dedup_in_one_window(self):
        """无时区后缀 hit_ts 与上海同时刻命中同一 30s 窗口：去重成功（只留一个）。"""
        window = DedupACWindow(window_ms=30000)
        naive = _hit("承诺减免", "2026-11-05T10:00:00")  # naive → Shanghai
        aware = _hit("承诺减免", "2026-11-05T10:00:10+08:00")  # 同时刻 aware
        result = window.apply([naive, aware])
        assert len(result) == 1, (
            "同一 30s 窗口内的同词命中应被去重（容器 UTC 下曾误判相差 8h 不去重）"
        )

    def test_naive_outside_window_not_deduped(self):
        """naive 与 aware 相差超窗：不去重（窗口语义正确性回归）。"""
        window = DedupACWindow(window_ms=30000)
        early = _hit("承诺减免", "2026-11-05T10:00:00")
        late = _hit("承诺减免", "2026-11-05T10:01:00+08:00")  # 相差 60s > 30s
        result = window.apply([early, late])
        assert len(result) == 2
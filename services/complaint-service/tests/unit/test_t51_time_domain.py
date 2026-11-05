"""T51 / 3.1：清理任务时间域统一（RecoveryService.cleanup_stale）单测。

验收判据：
- aware datetime 构造 25h 前 session 被清理；
- 残留 float 旧数据走 epoch 防御分支不崩、不误删；
- STALE_HOURS 边界正确（24h 整不清理 / 超 1s 清理）；
- 非法时间戳保守跳过；float epoch 时钟注入兼容（旧口径归一 UTC）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import time as time_mod
from datetime import datetime, timedelta, timezone

from app.service.recovery_service import STALE_HOURS, RecoveryService

_FIXED_NOW = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


class TestCleanupStaleTimeDomain:
    def test_aware_25h_session_cleaned(self):
        """aware datetime 构造 25h 前 session 被清理并置 pending_review。"""
        r = RecoveryService()
        sessions = {
            "s1": {
                "task_status": "pending",
                "updated_at": datetime.now(timezone.utc) - timedelta(hours=25),
            }
        }
        assert r.cleanup_stale(sessions) == ["s1"]
        assert sessions["s1"]["task_status"] == "pending_review"

    def test_float_legacy_data_not_crash_not_wrongly_cleaned(self):
        """残留 float 旧数据：epoch 防御分支收敛 UTC，25h 前清理、新鲜不清理。"""
        r = RecoveryService()
        sessions = {
            "old": {"task_status": "pending", "updated_at": time_mod.time() - 25 * 3600},
            "fresh": {"task_status": "pending", "updated_at": time_mod.time()},
        }
        assert r.cleanup_stale(sessions) == ["old"]

    def test_stale_hours_boundary(self):
        """STALE_HOURS 边界：24h 整不清理（严格大于），超 1s 清理，23h 不清理。"""
        r = RecoveryService(clock=lambda: _FIXED_NOW)
        sessions = {
            "boundary": {
                "task_status": "pending",
                "updated_at": _FIXED_NOW - timedelta(hours=STALE_HOURS),
            },
            "just_over": {
                "task_status": "pending",
                "updated_at": _FIXED_NOW - timedelta(hours=STALE_HOURS, seconds=1),
            },
            "recent": {
                "task_status": "pending",
                "updated_at": _FIXED_NOW - timedelta(hours=23),
            },
        }
        assert r.cleanup_stale(sessions) == ["just_over"]

    def test_invalid_timestamp_skipped_conservatively(self):
        """非法时间戳：保守跳过（不崩、不误标）。"""
        r = RecoveryService(clock=lambda: _FIXED_NOW)
        sessions = {"bad": {"task_status": "pending", "updated_at": "garbage"}}
        assert r.cleanup_stale(sessions) == []
        assert sessions["bad"]["task_status"] == "pending"

    def test_naive_datetime_bound_to_shanghai(self):
        """naive updated_at 按 Asia/Shanghai 解释（time_parse 单源，T51 口径）。"""
        naive_25h_ago_shanghai = (
            datetime.now() - timedelta(hours=25)  # naive 本地 wall time
        )
        r = RecoveryService()
        sessions = {"s1": {"task_status": "pending",
                           "updated_at": naive_25h_ago_shanghai}}
        assert r.cleanup_stale(sessions) == ["s1"]

    def test_float_epoch_clock_injection_compat(self):
        """float epoch 时钟注入（旧口径）：归一 UTC 后正常比较。"""
        r = RecoveryService(clock=lambda: time_mod.time())
        sessions = {
            "s1": {"task_status": "pending", "updated_at": time_mod.time() - 25 * 3600}
        }
        assert r.cleanup_stale(sessions) == ["s1"]

    def test_archived_closed_and_none_untouched(self):
        """archived/closed 与缺 updated_at：跳过（回归不变）。"""
        r = RecoveryService(clock=lambda: _FIXED_NOW)
        sessions = {
            "a": {"task_status": "archived",
                  "updated_at": _FIXED_NOW - timedelta(hours=100)},
            "n": {"task_status": "pending", "updated_at": None},
        }
        assert r.cleanup_stale(sessions) == []
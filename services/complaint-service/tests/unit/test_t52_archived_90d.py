"""T52 / 3.2：cleanup_archived_90d 时间域统一 + 缺时间戳保守跳过 单测。

验收判据（spec §4.5 兼容性钉死）：
- 缺 ``updated_at`` 的 archived 记录被保留（MUST NOT 以默认 0 参与比较）；
- aware datetime 的 90 天前记录被删、90 天内不删（90 天整边界严格小于）；
- float epoch 旧数据兼容（91 天前被删、新鲜不删）；
- 非法时间戳保守跳过；非 archived 状态不删（回归）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import time as time_mod
from datetime import datetime, timedelta, timezone

from app.service.scheduler import cleanup_archived_90d


class TestCleanupArchived90d:
    def test_missing_updated_at_archived_record_kept(self):
        """缺 updated_at 的 archived 记录保守保留（语义反转修复，本任务核心钉死）。"""
        store = {"s1": {"task_status": "archived"}}
        assert cleanup_archived_90d(store) == 0
        assert "s1" in store, "无时间戳记录 MUST NOT 被误删"

    def test_aware_datetime_90d_boundary(self):
        """aware datetime：90 天前被删；90 天整与 90 天内不删（严格小于）。

        注入固定 clock 消除构造与内部取时的微秒竞态。
        """
        fixed_now = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
        store = {
            "old": {"task_status": "archived",
                    "updated_at": fixed_now - timedelta(days=91)},
            "boundary": {"task_status": "archived",
                         "updated_at": fixed_now - timedelta(days=90)},
            "recent": {"task_status": "archived",
                       "updated_at": fixed_now - timedelta(days=89)},
        }
        assert cleanup_archived_90d(store, clock=lambda: fixed_now) == 1
        assert "old" not in store
        assert "boundary" in store
        assert "recent" in store

    def test_float_legacy_epoch_data(self):
        """float epoch 旧数据：91 天前被删、新鲜不删（不崩）。"""
        store = {
            "s1": {"task_status": "archived",
                   "updated_at": time_mod.time() - 91 * 86400},
            "s2": {"task_status": "archived", "updated_at": time_mod.time()},
        }
        assert cleanup_archived_90d(store) == 1
        assert "s1" not in store
        assert "s2" in store

    def test_invalid_timestamp_skipped(self):
        """非法时间戳：保守跳过不崩。"""
        store = {"s1": {"task_status": "archived", "updated_at": "garbage"}}
        assert cleanup_archived_90d(store) == 0
        assert "s1" in store

    def test_non_archived_untouched(self):
        """非 archived 状态即使超 90 天也不删（回归不变）。"""
        now = datetime.now(timezone.utc)
        store = {"s3": {"task_status": "pending",
                        "updated_at": now - timedelta(days=100)}}
        assert cleanup_archived_90d(store) == 0
        assert "s3" in store

    def test_none_store_returns_zero(self):
        """None store：返回 0（回归不变）。"""
        assert cleanup_archived_90d(None) == 0
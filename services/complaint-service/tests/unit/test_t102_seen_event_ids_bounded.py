"""T102：complaint subscriber `_seen_event_ids` 有界（spec runtime-correctness：常驻进程内存有界）。

set → dict 保序形态（O(1) 成员判断 + 插入序可见）+ 容量上限显式常量；超限按
插入序驱逐最旧，随机 event_id 刷事件不再永久驻留。生产去重主防线为 Redis
SET NX（TTL 24h），进程内集合仅在 Redis 不可用时兜底。
"""
from __future__ import annotations

import pytest

from app.events import subscriber as sub_mod


class TestT102SeenEventIdsBounded:
    def setup_method(self):
        sub_mod._seen_event_ids.clear()

    def teardown_method(self):
        sub_mod._seen_event_ids.clear()

    def test_seen_event_ids_length_bounded(self):
        """超上限写入：长度恒为上限，最旧被驱逐（FIFO）。"""
        for i in range(sub_mod._SEEN_EVENT_IDS_MAXLEN + 100):
            sub_mod._remember_event_id(f"evt-{i}")
        assert len(sub_mod._seen_event_ids) == sub_mod._SEEN_EVENT_IDS_MAXLEN
        assert "evt-0" not in sub_mod._seen_event_ids
        assert f"evt-{sub_mod._SEEN_EVENT_IDS_MAXLEN + 99}" in sub_mod._seen_event_ids

    @pytest.mark.asyncio
    async def test_claim_dedupe_semantics_unchanged(self, monkeypatch):
        """幂等语义回归：首次 True、重复 False——有界化不改变行为。"""

        class _FakeRedis:
            async def set(self, key, value, nx=False, ex=None):
                return True  # Redis 首占成功

        from common.resilience import _redis as redis_mod

        monkeypatch.setattr(redis_mod, "get_redis", lambda: _FakeRedis())
        assert await sub_mod._claim_event_id("evt-x") is True
        assert await sub_mod._claim_event_id("evt-x") is False  # 进程内命中
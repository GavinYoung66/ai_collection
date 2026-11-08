"""T101/T102：rag query_cache rewrite 位 + emitted/_SESSION_OWNERS 有界。

- T101（spec hybrid-search：缓存键语义完整性）：同一 query 以 rewrite=true 与
  rewrite=false 检索语义不同，MUST 使用不同缓存键；T75 长度前缀口径不回归；
- T102（spec runtime-correctness：常驻进程内存有界）：`emitted` deque(maxlen)、
  `_SESSION_OWNERS` OrderedDict + 容量上限 + 超限清扫。
"""
from __future__ import annotations

import time
from uuid import uuid4

from app.api.deps import (
    _ANON_SESSION_TTL_SECONDS,
    _SESSION_OWNERS,
    _SESSION_OWNERS_MAX,
    _bind_session_owner,
    _owner_of,
)
from app.events.resilience import _EMITTED_MAXLEN, clear_emitted, emitted
from app.services.query_cache import make_cache_key


class TestT101CacheKeyRewriteBit:
    def test_rewrite_bit_separates_cache_keys(self):
        """spec 场景：rewrite=true 与 rewrite=false 使用不同缓存键。"""
        k_true = make_cache_key("逾期协商", "regulation", 5, rewrite=True)
        k_false = make_cache_key("逾期协商", "regulation", 5, rewrite=False)
        assert k_true != k_false

    def test_default_rewrite_is_false(self):
        """不传 rewrite（缺省）与 rewrite=False 同 key（存量调用方语义不变）。"""
        assert make_cache_key("逾期协商", "regulation", 5) == make_cache_key(
            "逾期协商", "regulation", 5, rewrite=False
        )

    def test_same_params_stable(self):
        """同参数 key 稳定（回归不变）。"""
        assert make_cache_key("q", "regulation", 5) == make_cache_key(
            "q", "regulation", 5
        )

    def test_length_prefix_ambiguity_still_isolated(self):
        """T75 长度前缀口径不回归：拼接歧义参数仍隔离（spec 场景 3）。"""
        k1 = make_cache_key("ab", "c", 5)
        k2 = make_cache_key("abc", None, 5)
        assert k1 != k2


class TestT102EmittedBounded:
    def test_emitted_length_bounded(self):
        """1100 次事件后长度恒为上限，最旧被驱逐。"""
        clear_emitted()
        for i in range(_EMITTED_MAXLEN + 100):
            emitted.append(("rag.rate_limit.hit", {"i": i}))
        assert len(emitted) == _EMITTED_MAXLEN
        assert emitted[0][1]["i"] == 100  # FIFO 环形截断
        clear_emitted()


class TestT102SessionOwnersBounded:
    def setup_method(self):
        _SESSION_OWNERS.clear()

    def teardown_method(self):
        _SESSION_OWNERS.clear()

    def test_random_session_ids_bounded(self):
        """攻击者以随机 X-Session-Id 刷 10500 次：长度稳定在上限内。"""
        for _ in range(_SESSION_OWNERS_MAX + 500):
            _bind_session_owner(str(uuid4()), "anon")
        assert len(_SESSION_OWNERS) == _SESSION_OWNERS_MAX

    def test_expired_entries_recycled_on_overflow(self):
        """超限触发清扫：TTL 过期条目全部回收；灌满后新插入的活跃条目保留。"""
        expired_sid = "expired-sid"
        _SESSION_OWNERS[expired_sid] = (
            "anon",
            time.time() - _ANON_SESSION_TTL_SECONDS - 10,
        )
        # 灌满触发清扫（expired 过期条目被回收）
        for _ in range(_SESSION_OWNERS_MAX):
            _bind_session_owner(str(uuid4()), "anon")
        assert expired_sid not in _SESSION_OWNERS  # 过期条目被清扫回收
        assert len(_SESSION_OWNERS) == _SESSION_OWNERS_MAX
        # 灌满后插入 fresh：驱逐的是最旧灌入条目，fresh 位于尾部保留
        fresh_sid = "fresh-sid"
        _bind_session_owner(fresh_sid, "anon")
        assert _owner_of(fresh_sid) == "anon"
        assert len(_SESSION_OWNERS) == _SESSION_OWNERS_MAX

    def test_owner_ttl_semantics_unchanged(self):
        """TTL 读取语义回归：匿名 1h 过期、登录 24h。"""
        _SESSION_OWNERS["sid-a"] = ("anon", time.time() - _ANON_SESSION_TTL_SECONDS - 1)
        assert _owner_of("sid-a") is None  # 匿名过期
        _SESSION_OWNERS["sid-b"] = ("alice", time.time() - 10)
        assert _owner_of("sid-b") == "alice"  # 登录未过期
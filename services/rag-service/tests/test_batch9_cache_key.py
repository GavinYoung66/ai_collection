"""批次 9：rag-service 检索缓存 key 长度前缀（T75 / 9.2）单测。

验收判据：`query="ab",category="c"` 与 `query="abc",category=None` 两组歧义
参数生成不同 key（同身份不再串扰）；长度前缀格式符合 spec §6.5。
"""
from __future__ import annotations

from app.services.query_cache import make_cache_key


class TestCacheKeyLengthPrefix:
    def test_ambiguous_params_generate_distinct_keys(self):
        """拼接歧义场景：旧实现两组参数同 key 串扰，新实现（长度前缀）隔离。"""
        k1 = make_cache_key("ab", "c", 5)
        k2 = make_cache_key("abc", None, 5)
        assert k1 != k2, "长度前缀必须消除 query/category 拼接歧义"

    def test_same_params_generate_same_key(self):
        """同参数：key 稳定（回归不变）。"""
        assert make_cache_key("查询", "regulation", 5) == make_cache_key(
            "查询", "regulation", 5
        )

    def test_length_prefix_format(self):
        """key 原文含长度前缀（spec §6.5 格式：{len}:{query}|{category}|...）。"""
        from app.services import query_cache

        raw_len = len("查询")
        # 直接验证 md5 前的原文形态：重构 make_cache_key 的拼接段
        import hashlib

        raw = f"{raw_len}:查询|regulation|5|{query_cache._flags_hash()}|anon"
        expected = (
            query_cache._CACHE_KEY_PREFIX
            + hashlib.md5(raw.encode("utf-8")).hexdigest()
        )
        assert make_cache_key("查询", "regulation", 5, scope="anon") == expected

    def test_empty_query_still_distinct_from_category(self):
        """空 query + category 与非空 query 边界同样隔离。"""
        k1 = make_cache_key("", "regulation", 5)
        k2 = make_cache_key("regulation", None, 5)
        assert k1 != k2
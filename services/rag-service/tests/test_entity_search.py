"""实体 ID 结构化检索辅助逻辑测试。"""
from app.services.search_service import (
    _apply_entity_boost,
    _extract_entity_ids,
    _trim_entity_results,
)


class TestEntityIds:
    def test_extract_cs_id(self):
        assert _extract_entity_ids("查下 CS20260020 这个用户的情况") == ["CS20260020"]

    def test_boost_entity_match_to_top(self):
        items = [
            {"content": "无关歌词", "rerank_score": 0.9, "weight": 1.0},
            {"content": "用户编号:CS20260020 逾期258天", "rerank_score": 0.2, "weight": 1.0},
        ]
        out = _apply_entity_boost(items, ["CS20260020"])
        assert out[0]["entity_match"] is True
        assert out[0]["rerank_score"] >= 0.95
        assert "CS20260020" in out[0]["content"]

    def test_trim_keeps_only_entity_matches_when_present(self):
        items = _apply_entity_boost(
            [
                {"content": "协商还款合规话术", "rerank_score": 0.88},
                {"content": "用户 CS20260020 逾期", "rerank_score": 0.95, "entity_match": True},
            ],
            ["CS20260020"],
        )
        trimmed = _trim_entity_results(items, ["CS20260020"], top_k=5)
        assert len(trimmed) == 1
        assert "CS20260020" in trimmed[0]["content"]

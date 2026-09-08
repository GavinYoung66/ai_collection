"""V1-T17 整改测试：双闸门 0.6 接入检索流程。

闸门 1：稠密召回最高相似度 < RAG_SCORE_THRESHOLD(0.6) → 判定无匹配，走兜底；
ES-only 降级（无稠密相似度）跳过闸门 1，由闸门 2（rerank 0.35）兜底。
"""
import pytest

from unittest.mock import MagicMock

from app.services.search_service import SearchService
from app.settings import settings


def _make_service(dense_scores: list[float] | None, rerank_score: float = 0.9):
    class MockLogService:
        async def log(self, **kwargs):
            pass

    class MockMilvus:
        async def search(self, query_vector, top_n=20, category=None):
            if not dense_scores:
                raise ConnectionError("milvus down")
            return [
                {
                    "chunk_id": f"chunk-{i}",
                    "doc_id": f"doc-{i}",
                    "category": "regulation",
                    "score": s,
                }
                for i, s in enumerate(dense_scores, 1)
            ]

    class MockES:
        async def search(self, query, top_n=20, category=None):
            return [
                {
                    "chunk_id": "chunk-1",
                    "doc_id": "doc-1",
                    "category": "regulation",
                    "content": "内容1",
                    "score": 5.0,
                },
            ]

    class MockEmbed:
        async def embed(self, text):
            return [0.1] * 1024

    class MockRerank:
        async def is_degraded(self):
            return False

        async def rerank(self, query, candidates):
            return [
                {"chunk_id": c["chunk_id"], "rerank_score": rerank_score}
                for c in candidates
            ]

    return SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
    )


class TestGate1Threshold:
    @pytest.mark.asyncio
    async def test_low_dense_score_triggers_fallback(self):
        """稠密最高相似度 0.5 < 0.6 → 无匹配兜底，不执行 rerank 精排。"""
        svc = _make_service([0.5])
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] == []
        assert result["message"] is not None
        assert result["query_original"] == "测试"

    @pytest.mark.asyncio
    async def test_high_dense_score_returns_results(self):
        """稠密最高相似度 0.9 >= 0.6 → 正常检索（rerank 0.9 不触发闸门 2）。"""
        svc = _make_service([0.9])
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] != []
        assert result["message"] is None

    @pytest.mark.asyncio
    async def test_es_only_degraded_skips_gate1(self):
        """Milvus 宕机（ES-only 降级）→ 无稠密相似度，跳过闸门 1，正常返回结果。"""
        svc = _make_service(None)
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["degraded"] is True
        assert result["results"] != []

    @pytest.mark.asyncio
    async def test_boundary_score_exactly_threshold_returns(self, monkeypatch):
        """边界：稠密相似度恰等于 0.6 → 不触发闸门 1（>= 阈值放行）。"""
        monkeypatch.setattr(settings, "RAG_SCORE_THRESHOLD", 0.6)
        svc = _make_service([0.6])
        result = await svc.hybrid_search("测试", "regulation", 5)
        assert result["results"] != []


class TestGate2ThresholdNotPollutedByWeighting:
    """D-1 回归：闸门 2 的 max_rerank_score 取全局最大原始 rerank_score，不乘权重。

    场景：chunk-1 rerank=0.36(weight=1.0)、chunk-2 rerank=0.34(weight=1.5)。
    加权排序后 chunk-2 居首；若 max_rerank_score 误取排序首项（0.34<0.35）
    将错误触发闸门 2 兜底，吞掉原始分 0.36 的合格结果。
    """

    def _make_service(self, rerank_scores: dict[str, float], log_records: list):
        class MockLogService:
            async def log(self, **kwargs):
                log_records.append(kwargs)

        class MockDocRepo:
            def __init__(self):
                # chunk_index 补齐分支：返回空结果，避免触达真实 DB
                self.db = MagicMock()
                empty_result = MagicMock()
                empty_result.scalars.return_value.all.return_value = []

                async def _fake_execute(*args, **kwargs):
                    return empty_result

                self.db.execute = MagicMock(side_effect=_fake_execute)

            async def get_meta_dict(self, uid_list):
                return {}

            async def get_titles_map(self, uid_list):
                return {}

        class MockMilvus:
            async def search(self, query_vector, top_n=20, category=None):
                return [
                    {
                        "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "doc_id": "11111111-1111-1111-1111-111111111111",
                        "category": "case",
                        "content": "内容1",
                        "score": 0.9,
                    },
                    {
                        "chunk_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        "doc_id": "22222222-2222-2222-2222-222222222222",
                        "category": "case",
                        "content": "内容2",
                        "score": 0.9,
                    },
                ]

        class MockES:
            async def search(self, query, top_n=20, category=None):
                return [
                    {
                        "chunk_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "doc_id": "11111111-1111-1111-1111-111111111111",
                        "category": "case",
                        "content": "内容1",
                        "score": 5.0,
                    },
                    {
                        "chunk_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                        "doc_id": "22222222-2222-2222-2222-222222222222",
                        "category": "case",
                        "content": "内容2",
                        "score": 4.0,
                    },
                ]

        class MockEmbed:
            async def embed(self, text):
                return [0.1] * 1024

        class MockRerank:
            async def is_degraded(self):
                return False

            async def rerank(self, query, candidates):
                return [
                    {
                        "chunk_id": c["chunk_id"],
                        "rerank_score": rerank_scores.get(c["chunk_id"], 0.0),
                    }
                    for c in candidates
                ]

        def _apply(items, meta):
            for it in items:
                it["weight"] = (
                    1.5
                    if it["chunk_id"] == "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
                    else 1.0
                )
            return items

        weighting = MagicMock()
        weighting.apply = MagicMock(side_effect=_apply)

        return SearchService(
            milvus_client=MockMilvus(),
            es_client=MockES(),
            embed_client=MockEmbed(),
            rerank_client=MockRerank(),
            log_service=MockLogService(),
            doc_repo=MockDocRepo(),
            weighting_service=weighting,
        )

    @pytest.mark.asyncio
    async def test_gate2_uses_global_max_raw_score(self, monkeypatch):
        """加权项（0.34×1.5）居首但原始分 <0.35，另有 0.36 合格 → 不得误兜底。"""
        monkeypatch.setattr(settings, "KB_WEIGHTING_ENABLED", True)
        log_records: list = []
        svc = self._make_service(
            {
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": 0.36,
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": 0.34,
            },
            log_records,
        )
        result = await svc.hybrid_search("测试", "case", 5)

        # ① 不误触闸门 2 兜底（旧实现 max=0.34<0.35 → 空 results）
        assert result["results"] != []
        # ② 加权排序仍生效：chunk-2（0.34×1.5）居首
        assert (
            result["results"][0]["doc_id"]
            == "22222222-2222-2222-2222-222222222222"
        )
        assert result["results"][0]["rerank_score"] == pytest.approx(0.34)
        # ③ 日志 max_rerank_score = 全局最大原始分 0.36（不乘权重）
        score_logs = [r for r in log_records if r.get("max_rerank_score") is not None]
        assert score_logs
        assert score_logs[-1]["max_rerank_score"] == pytest.approx(0.36)

    @pytest.mark.asyncio
    async def test_gate2_global_max_below_threshold_still_fallback(self, monkeypatch):
        """全局最大原始分也 <0.35（0.32/0.30）→ 即使加权仍正确触发闸门 2 兜底。"""
        monkeypatch.setattr(settings, "KB_WEIGHTING_ENABLED", True)
        log_records: list = []
        svc = self._make_service(
            {
                "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa": 0.32,
                "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb": 0.30,
            },
            log_records,
        )
        result = await svc.hybrid_search("测试", "case", 5)

        # 全局最大 0.32 < 0.35 → 兜底（不因加权放大误放行）
        assert result["results"] == []
        assert result["message"] is not None
        score_logs = [r for r in log_records if r.get("max_rerank_score") is not None]
        assert score_logs
        assert score_logs[-1]["max_rerank_score"] == pytest.approx(0.32)

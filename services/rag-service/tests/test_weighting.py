"""业务加权服务单元测试（§9.6 / §16.5）。

覆盖：
- 新通话 + 高风险 = ×1.95（1.5 × 1.3）
- V1 文档（元数据全 None）= ×1.0 不变
- 开关关闭时 SearchService 不调用 WeightingService.apply（spy）
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.weighting_service import WeightingService


class TestWeightingService:
    def test_new_call_and_high_risk_multiplies_1_95(self):
        """近 7 天通话 + risk_tag=high → weight=1.5×1.3=1.95。"""
        recent_date = datetime.now(timezone.utc) - timedelta(days=2)
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "score": 0.10, "category": "case"},
        ]
        meta = {
            "d1": {
                "customer_id": None,
                "agent_id": None,
                "call_date": recent_date,
                "risk_tag": "high",
                "source": "asr",
                "version": 2,
            }
        }
        svc = WeightingService()
        result = svc.apply(items, meta)
        assert result[0]["weight"] == pytest.approx(1.95, rel=1e-3)
        assert result[0]["score"] == pytest.approx(0.10 * 1.95, rel=1e-3)

    def test_v1_doc_none_meta_weight_1(self):
        """V1 文档元数据全 None → weight=1.0，score 不变。"""
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "score": 0.05, "category": "regulation"},
        ]
        # V1 文档：get_meta_dict 回填 source='v1_legacy', version=1，其余 None
        meta = {
            "d1": {
                "customer_id": None,
                "agent_id": None,
                "call_date": None,
                "risk_tag": None,
                "source": "v1_legacy",
                "version": 1,
            }
        }
        svc = WeightingService()
        result = svc.apply(items, meta)
        assert result[0]["weight"] == 1.0
        assert result[0]["score"] == 0.05

    def test_old_call_no_boost(self):
        """call_date 超 7 天 → 不触发 ×1.5。"""
        old_date = datetime.now(timezone.utc) - timedelta(days=30)
        items = [{"chunk_id": "c1", "doc_id": "d1", "score": 0.08, "category": "case"}]
        meta = {
            "d1": {
                "customer_id": None,
                "agent_id": None,
                "call_date": old_date,
                "risk_tag": "low",
                "source": "asr",
                "version": 2,
            }
        }
        svc = WeightingService()
        result = svc.apply(items, meta)
        assert result[0]["weight"] == 1.0


class TestWeightingSwitchBypass:
    """§16.11 backward_compat 的子集：开关关闭时 SearchService 不调用 apply。"""

    def test_weighting_not_called_when_switch_off(self, monkeypatch):
        """KB_WEIGHTING_ENABLED=False → SearchService 不调用 WeightingService.apply。"""
        from common.config.settings import settings
        monkeypatch.setattr(settings, "KB_WEIGHTING_ENABLED", False)

        # 构造 SearchService，注入 spy WeightingService
        from app.services.search_service import SearchService

        mock_milvus = MagicMock()
        mock_milvus.search = AsyncMock(return_value=[])
        mock_es = MagicMock()
        mock_es.search = AsyncMock(return_value=[])
        mock_embed = MagicMock()
        mock_embed.embed = AsyncMock(return_value=[0.1] * 1024)
        mock_rerank = MagicMock()
        mock_rerank.is_degraded = AsyncMock(return_value=False)
        mock_rerank.rerank = AsyncMock(return_value=[])
        mock_log = MagicMock()
        mock_log.log = AsyncMock()

        weighting_svc = MagicMock()
        weighting_svc.apply = MagicMock(wraps=lambda items, meta: items)

        svc = SearchService(
            milvus_client=mock_milvus,
            es_client=mock_es,
            embed_client=mock_embed,
            rerank_client=mock_rerank,
            log_service=mock_log,
            weighting_service=weighting_svc,
        )
        # 双路召回都返回空 → 抛 A3005，但 apply 不应被调用
        import asyncio
        from common.errors.exceptions import BizException
        with pytest.raises(BizException):
            asyncio.get_event_loop().run_until_complete(
                svc.hybrid_search("测试", "regulation", 5)
            ) if False else asyncio.run(svc.hybrid_search("测试", "regulation", 5))
        weighting_svc.apply.assert_not_called()

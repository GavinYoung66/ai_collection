"""业务加权打分：按文档元数据放大 RRF 分值。"""
from datetime import datetime, timedelta, timezone

import structlog

logger = structlog.get_logger()


class WeightingService:
    """RRF 融合后、Rerank 前，对每个候选 chunk 按文档元数据计算权重系数。

    权重规则：
    - 新通话（call_date 近 7 天）×1.5
    - 高风险（risk_tag="high"）×1.3
    - 其他默认 ×1.0
    - V1 文档（全 None）weight=1.0 不变
    """

    def apply(self, items: list[dict], meta_dict: dict) -> list[dict]:
        """对 RRF items 施加业务权重，返回加权后的 items（score 已乘以 weight）。

        Args:
            items: RRF 融合后的候选列表，每项含 doc_id (str), score (float) 等
            meta_dict: {doc_id_str: {customer_id, agent_id, call_date, risk_tag, source, version}}
        Returns:
            加权后的 items 列表（原地修改 score）
        """
        now = datetime.now(timezone.utc)
        seven_days_ago = now - timedelta(days=7)

        for item in items:
            doc_id = item.get("doc_id", "")
            meta = meta_dict.get(doc_id, {})
            weight = 1.0

            call_date = meta.get("call_date")
            if call_date is not None:
                # 确保 timezone-aware 比较
                if call_date.tzinfo is None:
                    call_date = call_date.replace(tzinfo=timezone.utc)
                if call_date >= seven_days_ago:
                    weight *= 1.5

            risk_tag = meta.get("risk_tag")
            if risk_tag == "high":
                weight *= 1.3

            item["score"] = item.get("score", 0.0) * weight
            item["weight"] = weight

        return items

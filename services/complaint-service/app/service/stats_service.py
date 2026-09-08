"""统计服务（8.3）。

- 7 指标：P95 处理时长、循环重试均值、证据不足转人工占比、AI-人工一致率、
  MCP 失败率、LLM 降级率、驳回重判率
- by_verdict 四值分组
- 一致率连续两周 <80% 告警
- 热点缓存 csmp:hotspots:{start}:{end} TTL 6h
"""
from __future__ import annotations

import statistics
from datetime import datetime, timezone
from typing import Any, Optional

from app.models.state import VerdictType
from app.utils.redis_keys import TTL_HOTSPOTS, hotspots_key

_HOTSPOT_TTL = TTL_HOTSPOTS  # 6h
_CONSISTENCY_ALERT_THRESHOLD = 0.80

_VALID_VERDICTS = [v.value for v in VerdictType]


class _InMemoryRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v, ex=None):
        self._d[k] = v
        return True


class StatsService:
    def __init__(self, redis_client: Any = None,
                 consistency_history: Optional[list[float]] = None) -> None:
        self._redis = redis_client or _InMemoryRedis()
        # 模拟数据源（批次9+ 接 PG）
        self._processing_minutes: list[float] = [5, 8, 12, 15, 20, 30]
        self._retry_counts: list[int] = [1, 2, 0, 3, 1]
        self._evidence_insufficient_ratio = 0.15
        self._mcp_failure_rate = 0.03
        self._llm_degraded_rate = 0.05
        self._reject_rejudge_rate = 0.10
        # 最近两周一致率（百分比）
        self._consistency_history = consistency_history or [0.85, 0.88, 0.90, 0.87, 0.86, 0.84, 0.83,
                                                             0.82, 0.81, 0.80, 0.79, 0.78, 0.77, 0.76]
        self._by_verdict = {
            VerdictType.agent_violation.value: 42,
            VerdictType.user_misunderstanding.value: 18,
            VerdictType.compliant_but_poor_experience.value: 15,
            VerdictType.pending.value: 5,
        }

    def _p95(self, values: list[float]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        idx = int(len(s) * 0.95)
        return float(s[min(idx, len(s) - 1)])

    def get_consistency_rate(self) -> float:
        return self._consistency_history[-1] if self._consistency_history else 0.0

    def check_consistency_alert(self) -> bool:
        """连续两周一致率 <80% → 告警。"""
        if len(self._consistency_history) < 2:
            return False
        return all(r < _CONSISTENCY_ALERT_THRESHOLD
                   for r in self._consistency_history[-2:])

    def get_stats(self, start: str = "", end: str = "") -> dict[str, Any]:
        # 热点缓存
        cache_key = hotspots_key(start or "all", end or "all")
        cached = self._redis.get(cache_key)
        if cached:
            import json
            try:
                return json.loads(cached)
            except Exception:
                pass

        consistency = self.get_consistency_rate()
        result = {
            "p95_processing_minutes": self._p95(self._processing_minutes),
            "avg_retry_count": statistics.mean(self._retry_counts) if self._retry_counts else 0,
            "evidence_insufficient_ratio": self._evidence_insufficient_ratio,
            "ai_human_consistency_rate": consistency,
            "mcp_failure_rate": self._mcp_failure_rate,
            "llm_degraded_rate": self._llm_degraded_rate,
            "reject_rejudge_rate": self._reject_rejudge_rate,
            "by_verdict": dict(self._by_verdict),
            "consistency_alert": self.check_consistency_alert(),
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        # 写热点缓存 TTL 6h
        import json
        self._redis.set(cache_key, json.dumps(result), ex=_HOTSPOT_TTL)
        return result


# 全局单例
stats_service = StatsService()

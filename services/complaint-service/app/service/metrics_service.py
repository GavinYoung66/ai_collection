"""指标采集服务（8.6）。

自建薄 MetricsCollector（common.monitor.metrics 尚未实现，接口对齐后续可替换）：
- 业务 / 系统 / Agent 三类指标
- 告警阈值：一致率<80%(两周) / MCP失败>5% / LLM降级>10% / 队列积压>500
- 采集间隔 60s，MockClock 可测
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional


class MockClock:
    """可快进的 Mock 时钟，用于测试驱动采集。"""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


# 告警阈值
_ALERT_THRESHOLDS = {
    "ai_human_consistency_rate": (0.80, "below"),   # 连续两周 <80%
    "mcp_failure_rate": (0.05, "above"),              # >5%
    "llm_degraded_rate": (0.10, "above"),             # >10%
    "queue_backlog": (500, "above"),                  # >500
}

COLLECT_INTERVAL = 60  # 秒


class MetricsCollector:
    def __init__(self) -> None:
        self._gauges: dict[str, float] = {}
        self._counters: dict[str, float] = defaultdict(float)
        # 一致率历史（两周）
        self._consistency_history: list[float] = []

    def record(self, name: str, value: float) -> None:
        if name == "ai_human_consistency_rate":
            self._consistency_history.append(value)
            if len(self._consistency_history) > 14:
                self._consistency_history.pop(0)
        self._gauges[name] = value

    def inc(self, name: str, delta: float = 1.0) -> None:
        self._counters[name] += delta

    def get(self, name: str) -> float:
        return self._gauges.get(name, 0.0)

    def evaluate_alerts(self) -> list[dict]:
        alerts = []
        for metric, (threshold, op) in _ALERT_THRESHOLDS.items():
            val = self._gauges.get(metric)
            if val is None:
                continue
            triggered = (val < threshold) if op == "below" else (val > threshold)
            # 一致率需连续两周
            if metric == "ai_human_consistency_rate" and len(self._consistency_history) >= 2:
                triggered = all(r < threshold for r in self._consistency_history[-2:])
            if triggered:
                alerts.append({"metric": metric, "value": val, "threshold": threshold})
        return alerts


class MetricsService:
    def __init__(self, clock: Optional[Any] = None) -> None:
        self.clock = clock or MockClock()
        self.collector = MetricsCollector()
        self._last_collect = self.clock.now() if hasattr(self.clock, "now") else 0

    def record(self, name: str, value: float) -> None:
        """tasks 冒烟入口：委托 collector.record。"""
        self.collector.record(name, value)

    def evaluate_alerts(self) -> list[dict]:
        """tasks 冒烟入口：委托 collector.evaluate_alerts。"""
        return self.collector.evaluate_alerts()

    def collect(self) -> dict:
        """执行一次采集（由定时任务或测试驱动）。"""
        self._last_collect = self._now()
        return {
            "collected_at": self._last_collect,
            "gauges": dict(self.collector._gauges),
            "alerts": self.collector.evaluate_alerts(),
        }

    def maybe_collect(self) -> Optional[dict]:
        """间隔 60s 自动采集（Mock 时钟可测）。"""
        now = self._now()
        if now - self._last_collect >= COLLECT_INTERVAL:
            return self.collect()
        return None

    def _now(self) -> float:
        return self.clock.now() if hasattr(self.clock, "now") else self.clock()


# 全局单例
metrics_service = MetricsService()

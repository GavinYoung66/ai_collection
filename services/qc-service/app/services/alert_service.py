"""高危告警资格与内存行。落库由 ReplayService 在终态 INSERT；本类不 PUBLISH。

资格：仅 MergeHits 终态 speaker=agent 且 severity=high；排除 R05；user high / 未合并重复不入。
structlog emit_alert_raised 保留；qc.alert.raised + XADD 仍由 ReplayService 落库后发一次。
"""
from __future__ import annotations

from typing import Any, Sequence
from uuid import uuid4

from app.observability import emit_alert_raised
from app.services.ac_filter import WordHit


def is_alert_eligible(hit: WordHit) -> bool:
    if hit.speaker != "agent" or hit.severity != "high":
        return False
    if hit.rule_id == "R05":
        return False
    return True


class AlertService:
    def __init__(self) -> None:
        self.alerts: list[dict[str, Any]] = []

    def on_high_risk(
        self,
        merged_hits: Sequence[WordHit],
        *,
        qc_record_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """只消费合并后 hits。user high / R05 / 未合并重复不得写入。"""
        written: list[dict[str, Any]] = []
        for hit in merged_hits:
            if not is_alert_eligible(hit):
                continue
            row = {
                "id": str(uuid4()),
                "qc_record_id": qc_record_id,
                "level": "high",
                "status": "NEW",
                "rule_id": hit.rule_id,
                "speaker": hit.speaker,
                "severity": hit.severity,
                "redline_tag": hit.redline_tag,
                "evidence": hit.evidence_text,
                "turn_id": hit.turn_id,
            }
            written.append(row)
            emit_alert_raised(
                call_id=None,
                level="high",
                evidence=hit.evidence_text,
            )
        self.alerts.extend(written)
        return written

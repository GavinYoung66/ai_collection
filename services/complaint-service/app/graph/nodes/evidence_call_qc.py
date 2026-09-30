"""evidence_call_qc 证据采集-通话质检 Agent 节点。

通过 MCP 拉取 ASR 文本、质检违规片段、告警元数据，写入新 CaseEvidenceBundle。
置信度降级复用批次2 `apply_corruption_degradation`（confidence_level）：
- ASR 乱码 is_corrupted → medium
- 录音残缺 / 核心内容缺失 → low
禁止合规定性。
"""
from __future__ import annotations

import re

import structlog

from app.api.deps import get_mcp_client
from app.mcp.errors import MCPError, MCPTimeoutError
from app.models.state import CaseEvidenceBundle, ComplaintSessionState, ExceptionInfo
from app.utils.evidence_helper import apply_corruption_degradation

logger = structlog.get_logger()

_SERVER = "call_qc"
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _is_corrupted(text: str) -> bool:
    """判断 ASR 文本是否乱码：CJK 占比过低或含大量特殊符号。"""
    if not text:
        return False  # 空内容由 core_content_missing / apply_corruption 处理为 low
    cjk = len(_CJK_RE.findall(text))
    return cjk / max(len(text), 1) < 0.3


def _is_incomplete(asr_segments: list, call_duration: int = 0) -> bool:
    """判断录音是否残缺：无 ASR 片段或时长为 0。"""
    return not asr_segments or call_duration <= 0


async def evidence_call_qc_node(state: ComplaintSessionState) -> dict:
    """通话质检证据采集节点。"""
    mcp = get_mcp_client()
    case_evidence: dict[str, CaseEvidenceBundle] = {}

    for case_id in state.case_ids:
        try:
            asr_data = await mcp.call(
                _SERVER, "query_call_asr", {"case_id": case_id},
                trace_id=state.trace_id, session_id=state.session_id,
            )
            qc_data = await mcp.call(
                _SERVER, "query_qc_violations", {"case_id": case_id},
                trace_id=state.trace_id, session_id=state.session_id,
            )
        except (MCPTimeoutError, MCPError) as e:
            logger.warning("evidence_call_qc_mcp_error", case_id=case_id, error=str(e))
            return {
                "exception_info": ExceptionInfo(
                    error_type=type(e).__name__, message=str(e), node="evidence_call_qc"
                ).model_dump()
            }

        asr_list = asr_data if isinstance(asr_data, list) else [asr_data]
        call_evidence: list[dict] = []
        for seg in asr_list:
            if not isinstance(seg, dict):
                continue
            segments = seg.get("asr_text_with_timestamp") or []
            full_text = " ".join(s.get("text", "") for s in segments if isinstance(s, dict))
            duration = seg.get("call_duration", 0) or 0
            incomplete = _is_incomplete(segments, duration)
            corrupted = _is_corrupted(full_text)
            call_evidence.append(
                {
                    "evidence_id": seg.get("call_session_id", f"asr-{case_id}"),
                    "evidence_type": "call_asr",
                    "source": "call",
                    "content": full_text,
                    "confidence": 1.0,
                    "confidence_level": "high",
                    "is_corrupted": corrupted or incomplete,
                    "core_content_missing": incomplete or not full_text.strip(),
                    "metadata": {"call_duration": duration},
                }
            )
        call_evidence = apply_corruption_degradation(call_evidence)

        # query_qc_violations 返回的是违规片段（fixture 形状含 qc_id/violation_type/
        # snippet_text），非告警（无 alert_id/description）。按违规字段装配 qc_alerts：
        # evidence_id 取 qc_id（qc-service 直连场景兜底 qc_record_id），content 取违规片段文本。
        qc_list = qc_data if isinstance(qc_data, list) else [qc_data]
        qc_alerts: list[dict] = []
        for alert in qc_list:
            if not isinstance(alert, dict):
                continue
            qc_alerts.append(
                {
                    "evidence_id": str(alert.get("qc_id") or alert.get("qc_record_id") or f"qc-{case_id}"),
                    "evidence_type": "qc_alert",
                    "source": "qc",
                    "content": str(alert.get("snippet_text") or alert.get("violation_type") or ""),
                    "confidence": 1.0,
                    "confidence_level": "high",
                    "is_corrupted": False,
                    "metadata": alert,
                }
            )

        case_evidence[case_id] = CaseEvidenceBundle(
            case_id=case_id, call_evidence=call_evidence, qc_alerts=qc_alerts
        )

    return {"case_evidence": case_evidence}

"""证据工具函数：展平 + 冲突解决 6 条规则（PRD 6.1 / 任务 2.6）。

证据 item 为 dict，关键字段对齐 PRD 6.1 EvidenceItem：
    evidence_id, source, case_id, content, confidence, confidence_level, is_corrupted
"""
from __future__ import annotations

from typing import Any

# ── 证据来源优先级（数值越小优先级越高） ──
# 结构化数据 > 通话ASR > 质检告警 > 历史工单 > 历史投诉 > 用户自述
SOURCE_PRIORITY: dict[str, int] = {
    "structured_data": 0,
    "call": 1,
    "qc": 2,
    "history_ticket": 3,
    "history_complaint": 4,
    "user_statement": 5,
}
_DEFAULT_PRIORITY = 99

# ── confidence_level 合法值 ──
LEVEL_HIGH = "high"
LEVEL_MEDIUM = "medium"
LEVEL_LOW = "low"
_VALID_LEVELS = {LEVEL_HIGH, LEVEL_MEDIUM, LEVEL_LOW}


def _collect_all_evidence(state: dict[str, Any]) -> list[dict[str, Any]]:
    """展平 case_evidence + cross_case_evidence + historical_complaint_evidence。

    - case_evidence: {case_id: CaseEvidenceBundle}，bundle 内含 case_info /
      call_evidence / history_tickets / history_complaints / qc_alerts
    - cross_case_evidence: list[EvidenceItem]
    - historical_complaint_evidence: list[EvidenceItem]
    """
    items: list[dict[str, Any]] = []

    # case_evidence（按案件分组）
    case_evidence = state.get("case_evidence") or {}
    if isinstance(case_evidence, dict):
        for case_id, bundle in case_evidence.items():
            if hasattr(bundle, "model_dump"):
                bundle = bundle.model_dump()
            if not isinstance(bundle, dict):
                continue
            case_info = bundle.get("case_info") or {}
            if isinstance(case_info, dict) and case_info:
                items.append(
                    {
                        "evidence_id": f"case-info-{case_id}",
                        "evidence_type": "case_info",
                        "source": "structured_data",
                        "case_id": case_id,
                        "content": str(case_info)[:2000],
                        "confidence": 1.0,
                        "confidence_level": "high",
                        "is_corrupted": False,
                        "metadata": case_info,
                    }
                )
            for field in ("call_evidence", "history_tickets", "history_complaints", "qc_alerts"):
                for ev in bundle.get(field) or []:
                    if isinstance(ev, dict):
                        # 原始 MCP 工单/投诉 dict 也可能没有 content，兜底序列化
                        if not ev.get("content"):
                            ev = {
                                **ev,
                                "content": str(ev)[:1500],
                                "source": ev.get("source")
                                or (
                                    "history_ticket"
                                    if field == "history_tickets"
                                    else "history_complaint"
                                    if field == "history_complaints"
                                    else "structured_data"
                                ),
                                "confidence_level": ev.get("confidence_level") or "high",
                            }
                        ev.setdefault("case_id", case_id)
                        items.append(ev)

    # cross_case_evidence
    for ev in state.get("cross_case_evidence") or []:
        if isinstance(ev, dict):
            items.append(ev)

    # historical_complaint_evidence
    for ev in state.get("historical_complaint_evidence") or []:
        if isinstance(ev, dict):
            items.append(ev)

    return items


def sort_by_priority(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则1：按来源优先级排序（结构化数据 > 通话ASR > 质检告警 > 历史工单 > 历史投诉 > 用户自述）。"""
    return sorted(items, key=lambda e: SOURCE_PRIORITY.get(e.get("source", ""), _DEFAULT_PRIORITY))


def filter_by_confidence(
    items: list[dict[str, Any]], min_level: str = LEVEL_MEDIUM
) -> list[dict[str, Any]]:
    """规则2：按置信度等级过滤。

    - high：完全可信，正常参与判责
    - medium：存疑，仅作辅助参考（auxiliary=True，不滤除）
    - low：不可信，不参与判责，仅在证据链中呈现给人工（被滤除）

    min_level 控制最低参与等级：low 表示全部保留（含 low）；
    medium 表示滤除 low；high 表示仅保留 high。
    """
    level_order = {LEVEL_LOW: 0, LEVEL_MEDIUM: 1, LEVEL_HIGH: 2}
    threshold = level_order.get(min_level, 1)
    result = []
    for ev in items:
        lvl = ev.get("confidence_level", LEVEL_HIGH)
        if lvl not in _VALID_LEVELS:
            lvl = LEVEL_HIGH
        if level_order[lvl] >= threshold:
            result.append(ev)
    return result


def mark_medium_auxiliary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则3：medium 等级仅作辅助参考，标记 auxiliary=True，不作核心判责依据。"""
    for ev in items:
        if ev.get("confidence_level") == LEVEL_MEDIUM:
            ev["auxiliary"] = True
    return items


def detect_core_conflict(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则4：检测核心事实冲突（金额/天数矛盾），标记 conflict_highlight=True。

    简单实现：若证据中存在对同一 case_id 的金额/天数描述差异较大，标记冲突。
    返回被标记为 conflict_highlight 的 item 列表。
    """
    # 按 case_id 分组，检测金额/天数关键词矛盾
    conflicts: list[dict[str, Any]] = []
    by_case: dict[str, list[dict[str, Any]]] = {}
    for ev in items:
        cid = ev.get("case_id") or "__nocase__"
        by_case.setdefault(cid, []).append(ev)

    for cid, evs in by_case.items():
        if len(evs) < 2:
            continue
        # 提取 content 中的数字（金额/天数）
        nums_per_ev = []
        for ev in evs:
            content = str(ev.get("content", ""))
            nums = [int(n) for n in __import__("re").findall(r"\d+", content)]
            nums_per_ev.append(set(nums))
        # 若两条证据的数字集合无交集且都非空，视为核心冲突
        for i in range(len(evs)):
            for j in range(i + 1, len(evs)):
                if nums_per_ev[i] and nums_per_ev[j] and nums_per_ev[i].isdisjoint(nums_per_ev[j]):
                    evs[i]["conflict_highlight"] = True
                    evs[j]["conflict_highlight"] = True
                    if evs[i] not in conflicts:
                        conflicts.append(evs[i])
                    if evs[j] not in conflicts:
                        conflicts.append(evs[j])
    return conflicts


def apply_corruption_degradation(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则5：ASR 残缺降级（PRD 7.3）。

    - is_corrupted=True：confidence_level → medium（残缺但仍可参考）
    - core_content_missing=True 或 content 为空：confidence_level → low（核心内容缺失，不可信）
    若同时命中，以更严格的 low 为准。
    """
    for ev in items:
        core_missing = ev.get("core_content_missing") or not ev.get("content")
        if core_missing:
            ev["confidence"] = 0.0
            ev["confidence_level"] = LEVEL_LOW
            ev["corrupted_degraded"] = True
        elif ev.get("is_corrupted"):
            ev["confidence"] = 0.3
            ev["confidence_level"] = LEVEL_MEDIUM
            ev["corrupted_degraded"] = True
    return items


def mark_conflict_pending(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """规则6：冲突证据不下放产出物生成 Agent（标记 pending_for_package=True）。"""
    for ev in items:
        if ev.get("conflict_highlight"):
            ev["pending_for_package"] = True
    return items

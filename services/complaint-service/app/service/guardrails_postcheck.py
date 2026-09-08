"""L4 后置合规校验（10.4）。

对齐 PRD 12.1 L4 层 + 12.2 红线 7 条（design D8 本批范围）：
1. verdict 四值枚举校验（无 normal）
2. regulation_refs / rag_source_ids 必须来自 RAG doc_id
3. 答辩幻觉检测：金额/天数/政策未命中 RAG → 替换「以坐席核实为准」
4. AC 红线扫描：暴力/威胁/伪造身份 → 命中则兜底替换
5. 红线3：坐席违规（agent_violation）时写入 stop_call_flag stub

失败重试≤3 次（调用方传 retry_fn），仍失败降级 RuleBased 兜底。
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Any, Callable, Optional

from common.guardrails import postcheck_text

# 合法 verdict 四值（与 app.models.state.VerdictType 对齐，不含 normal）
_VALID_VERDICTS = {
    "agent_violation",
    "user_misunderstanding",
    "compliant_but_poor_experience",
    "pending",
}

# D8 红线1/2：暴力威胁 + 伪造身份（法院/公安/律师等）
# 注意：不要用单字「威胁/暴力」——研判答辩常需描述「坐席威胁性话术」，会被误杀成兜底短文
_REDLINE_WORDS = [
    "弄死",
    "弄残",
    "上门打",
    "恐吓你",
    "我是法院",
    "我们是公安",
    "我是律师",
    "我是警察",
    "我们是法院",
    "我们是检察院",
]

# 兜底答辩文本（红线/幻觉命中时替换）
_SAFE_RESPONSE_FALLBACK = "经核实，相关情况正在进一步核查中，具体以坐席核实为准。"

# 金额 / 天数 正则（幻觉检测）
_AMOUNT_RE = re.compile(r"\d+(?:\.\d+)?\s*元")
_DAYS_RE = re.compile(r"\d+\s*天")


def _normalize_verdict(value: Any) -> str:
    """将 Enum / 字符串统一为 verdict 四值字符串。

    注意：Python 对 ``str, Enum`` 成员 ``str(member)`` 可能得到
    ``'VerdictType.xxx'``，必须取 ``.value``。
    """
    if value is None:
        return ""
    if isinstance(value, Enum):
        return str(value.value)
    text = str(value)
    if text.startswith("VerdictType."):
        return text.split(".", 1)[-1]
    return text


def _extract_amounts_and_days(text: str) -> list[str]:
    if not text:
        return []
    return _AMOUNT_RE.findall(text) + _DAYS_RE.findall(text)


def _redline_hit(text: str) -> bool:
    """AC 自动机红线扫描（暴力/威胁/伪造身份）— 经 common.guardrails。"""
    if not text:
        return False
    return not postcheck_text(text, words=_REDLINE_WORDS).ok


def _rule_based_fallback(package: dict) -> dict:
    """RuleBased 兜底：返回安全可用的最小判责包。"""
    return {
        "verdict": "pending",
        "confidence": 0.0,
        "reasoning": "L4 校验未通过，降级为 RuleBased 兜底。",
        "response_draft": _SAFE_RESPONSE_FALLBACK,
        "rag_source_ids": [],
        "regulation_refs": [],
        "stop_call_flag": False,
    }


def postcheck(
    package: dict[str, Any],
    rag_doc_ids: list[str],
    *,
    rag_text: str = "",
    max_retries: int = 3,
    retry_fn: Optional[Callable[[], dict[str, Any]]] = None,
) -> tuple[bool, dict[str, Any]]:
    """L4 后置校验。

    Args:
        package: 判责包，含 verdict / response_draft / rag_source_ids / regulation_refs
        rag_doc_ids: 本次 RAG 返回的合法 doc_id 集合（禁止用 package 自身 source 自证）
        rag_text: RAG 检索到的文本（用于幻觉命中比对）
        max_retries: 校验失败最多重试次数
        retry_fn: 重试时重新生成 package 的回调（无则不重试，直接兜底）

    Returns:
        (passed, sanitized_package)
        - passed=True：校验通过（可能已 sanitized 幻觉/红线）
        - passed=False：重试耗尽，返回 RuleBased 兜底
    """
    current = dict(package)

    for attempt in range(max_retries + 1):
        response_draft = str(current.get("response_draft", "") or "")

        # 1. verdict 四值校验（JudgmentPackage 可能无 verdict 字段：缺省跳过）
        raw_verdict = current.get("verdict", None)
        if raw_verdict is None or str(raw_verdict).strip() == "":
            verdict = ""
            verdict_ok = True
        else:
            verdict = _normalize_verdict(raw_verdict)
            verdict_ok = verdict in _VALID_VERDICTS

        # 2. 来源校验：非法 id 直接剔除；不因模型胡编 id 整包兜底
        allowed = set(rag_doc_ids or [])
        src_ids = list(current.get("rag_source_ids", []) or [])
        reg_refs = list(current.get("regulation_refs", []) or [])
        src_ids_clean = [s for s in src_ids if s in allowed] if allowed else []
        reg_refs_clean = [r for r in reg_refs if r in allowed] if allowed else []
        # 无合法 RAG 白名单时：清空引用，避免假 id 触发整包失败
        if not allowed:
            src_ids_clean, reg_refs_clean = [], []
        source_ok = True

        # 3/4. 先对原文做 AC，再做幻觉替换（避免替换后漏检）
        redline = _redline_hit(response_draft)
        amounts_days = _extract_amounts_and_days(response_draft)
        hallucination = any(tok not in (rag_text or "") for tok in amounts_days)
        if redline or hallucination:
            response_draft = _SAFE_RESPONSE_FALLBACK

        current["verdict"] = verdict if verdict_ok else verdict
        current["rag_source_ids"] = src_ids_clean
        current["regulation_refs"] = reg_refs_clean
        current["response_draft"] = response_draft

        # 综合判定：verdict 与来源必须合法；幻觉/红线已就地 sanitize
        if verdict_ok and source_ok:
            # 红线3 stub：坐席违规确认后写停止致电标记
            current["stop_call_flag"] = verdict == "agent_violation"
            return True, current

        # 失败 → 重试（若提供 retry_fn）
        if retry_fn is not None and attempt < max_retries:
            try:
                current = dict(retry_fn())
                continue
            except Exception:
                break
        break

    # 重试耗尽 → RuleBased 兜底
    return False, _rule_based_fallback(current)

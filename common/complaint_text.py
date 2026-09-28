"""差异化 complaint_text 组装（纯函数，Decision 3 / tasks 5.1）。

正文不上 EventBus；投诉侧按 case 反查违规后调用本模块。
分类主信号：qc_violations.speaker（agent / user / 无）。
"""
from __future__ import annotations

from typing import Any

# redline_tag → 中文（与 qc-service REDLINE_TAG_VALUES 对齐）
REDLINE_LABEL: dict[str, str] = {
    "threat": "威胁恐吓",
    "fake_identity": "冒充身份",
    "stop_call": "违规骚扰",
    "fake_commit": "虚假承诺",
    "legal_overstate": "夸大法律后果",
    "unverified": "未核实信息",
    "data_fabrication": "数据造假",
    "privacy_leak": "隐私泄露",
}

TEMPLATE_AGENT_VIOLATION = (
    "质检发现坐席通话中存在违规话术（{violations}），"
    "客户{customer}欠款{overdue_amount}元，请研判是否构成违规投诉并采取相应处置。"
)
TEMPLATE_USER_MISUNDERSTANDING = (
    "质检发现通话中客户侧存在异议（{violations}），"
    "客户{customer}欠款{overdue_amount}元，请研判客户诉求是否成立并跟进沟通。"
)
TEMPLATE_COMPLIANT = (
    "质检未发现违规项，通话合规。客户{customer}欠款{overdue_amount}元，"
    "请按正常催收流程继续跟进。"
)
FALLBACK_COMPLAINT_TEXT = "质检完成，请研判该通话是否构成违规投诉。"

CATEGORY_AGENT = "agent_violation"
CATEGORY_USER = "user_misunderstanding"
CATEGORY_COMPLIANT = "compliant"


def summarize_violations(violations: list[dict[str, Any]], speaker: str) -> str:
    """把指定 speaker 的违规项汇总为中文短语列表。"""
    seen: list[str] = []
    for v in violations:
        if str(v.get("speaker") or "") != speaker:
            continue
        tag = str(v.get("redline_tag") or "")
        label = REDLINE_LABEL.get(tag, tag or "违规")
        if label not in seen:
            seen.append(label)
    if not seen:
        for v in violations:
            if str(v.get("speaker") or "") != speaker:
                continue
            rid = str(v.get("rule_id") or "")
            if rid and rid not in seen:
                seen.append(rid)
    return "、".join(seen) if seen else "未明示违规项"


def classify_violations(violations: list[dict[str, Any]]) -> str:
    """按 speaker 分类：agent > user > compliant。"""
    has_agent = any(str(v.get("speaker") or "") == "agent" for v in violations)
    has_user = any(str(v.get("speaker") or "") == "user" for v in violations)
    if has_agent:
        return CATEGORY_AGENT
    if has_user:
        return CATEGORY_USER
    return CATEGORY_COMPLIANT


def build_complaint_text(
    *,
    violations: list[dict[str, Any]],
    customer: str = "客户",
    overdue_amount: str = "未知",
) -> tuple[str, str]:
    """组装差异化正文。

    Returns:
        (category, complaint_text)
    """
    customer = customer or "客户"
    overdue_amount = overdue_amount if overdue_amount not in (None, "") else "未知"
    category = classify_violations(violations)
    if category == CATEGORY_AGENT:
        summary = summarize_violations(violations, "agent")
        text = TEMPLATE_AGENT_VIOLATION.format(
            violations=summary, customer=customer, overdue_amount=overdue_amount
        )
    elif category == CATEGORY_USER:
        summary = summarize_violations(violations, "user")
        text = TEMPLATE_USER_MISUNDERSTANDING.format(
            violations=summary, customer=customer, overdue_amount=overdue_amount
        )
    else:
        text = TEMPLATE_COMPLIANT.format(
            customer=customer, overdue_amount=overdue_amount
        )
    return category, text

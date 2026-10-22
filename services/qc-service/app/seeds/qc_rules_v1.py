"""qc_rules v1.0 默认 active 包（DD-02 §5.2.3 R14 十八词 + penalty/window/deduction_policy）。

deduction_policy：rule_id → {clause_no, clause_text} 静态制度条款映射，
外加 __ac_default__ 兜底节点（by_redline_tag 优先、by_severity 次之），
供纯 AC 无 rule_id 的 hit 映射条款。不携带 points（由 severity→penalty 确定性推出）。
"""
from __future__ import annotations

from typing import Any

R14_KEYWORDS: tuple[str, ...] = (
    "起诉",
    "立案",
    "强制执行",
    "拘留",
    "坐牢",
    "判刑",
    "刑事责任",
    "逮捕",
    "征信花了",
    "征信洗白",
    "上黑名单",
    "失信被执行",
    "限制高消费",
    "追究法律责任",
    "冻结银行卡",
    "影响子女",
    "断供坐牢",
    "无法贷款",
)

FORBIDDEN_R14_KEYWORD = "我是法院的"

PENALTY_V1: dict[str, int] = {"high": 20, "medium": 8, "low": 2}
SAME_WORD_WINDOW_MS_V1 = 30000
QC_RULES_VERSION_V1 = "v1.0"

_RULE_META: tuple[tuple[str, str, str, str | None], ...] = (
    ("R01", "keyword", "high", "privacy_leak"),
    ("R02", "duration", "medium", None),
    ("R03", "keyword", "medium", "fake_commit"),
    ("R04", "keyword", "medium", "privacy_leak"),
    ("R05", "keyword", "low", "threat"),
    ("R06", "silence", "low", None),
    ("R07", "duration", "medium", None),
    ("R08", "keyword", "medium", "fake_commit"),
    ("R09", "duration", "low", None),
    ("R10", "semantic", "high", "unverified"),
    ("R11", "keyword", "medium", "threat"),
    ("R12", "keyword", "high", "fake_identity"),
    ("R13", "keyword", "high", "stop_call"),
    ("R14", "keyword", "medium", "legal_overstate"),
    ("R15", "keyword", "high", "data_fabrication"),
)

# 条款编号引用内部制度《催收合规作业管理办法》；红线条款引用《催收合规红线手册》。
DEDUCTION_POLICY_V1: dict[str, Any] = {
    "R01": {
        "clause_no": "红线手册-6.1",
        "clause_text": "禁止向无关第三方泄露债务人债务、联系方式等隐私信息。",
    },
    "R02": {
        "clause_no": "管理办法-3.2",
        "clause_text": "单通通话时长须覆盖必要告知与沟通环节，禁止异常长通话骚扰债务人。",
    },
    "R03": {
        "clause_no": "红线手册-4.3",
        "clause_text": "禁止未经授权向债务人承诺减免、延期或撤销记录。",
    },
    "R04": {
        "clause_no": "红线手册-6.2",
        "clause_text": "禁止在通话中刺探、传播债务人及关联人敏感个人信息。",
    },
    "R05": {
        "clause_no": "红线手册-4.2",
        "clause_text": "禁止使用施压、贬损性语言影响债务人正常生活。",
    },
    "R06": {
        "clause_no": "管理办法-3.4",
        "clause_text": "通话过程中禁止长时间静默、挂机等消极沟通行为。",
    },
    "R07": {
        "clause_no": "管理办法-3.3",
        "clause_text": "通话须完成规定告知动作，禁止未充分沟通即结束通话。",
    },
    "R08": {
        "clause_no": "红线手册-4.3",
        "clause_text": "禁止未经授权向债务人承诺减免、延期或撤销记录。",
    },
    "R09": {
        "clause_no": "管理办法-3.5",
        "clause_text": "单通通话时长不得短于规定下限，确保告知义务履行到位。",
    },
    "R10": {
        "clause_no": "红线手册-5.1",
        "clause_text": "关键事实（金额、账期、身份）未经核实不得向债务人断言。",
    },
    "R11": {
        "clause_no": "红线手册-4.2",
        "clause_text": "禁止威胁、恐吓、侮辱债务人及其联系人。",
    },
    "R12": {
        "clause_no": "红线手册-4.1",
        "clause_text": "禁止冒充公检法等国家机关工作人员或伪造身份开展催收。",
    },
    "R13": {
        "clause_no": "红线手册-4.5",
        "clause_text": "债务人明确要求停止呼叫后，不得再次拨打或变相骚扰。",
    },
    "R14": {
        "clause_no": "红线手册-4.4",
        "clause_text": "禁止夸大法律后果（起诉、拘留、征信黑名单等）误导、胁迫债务人。",
    },
    "R15": {
        "clause_no": "红线手册-5.2",
        "clause_text": "禁止伪造、篡改业务数据或捏造还款事实。",
    },
    # 纯 AC 无 rule_id 的 hit 兜底：redline_tag 优先，severity 次之。
    "__ac_default__": {
        "by_redline_tag": {
            "threat": {
                "clause_no": "红线手册-4.2",
                "clause_text": "禁止威胁、恐吓、侮辱债务人及其联系人。",
            },
            "fake_identity": {
                "clause_no": "红线手册-4.1",
                "clause_text": "禁止冒充公检法等国家机关工作人员或伪造身份开展催收。",
            },
            "stop_call": {
                "clause_no": "红线手册-4.5",
                "clause_text": "债务人明确要求停止呼叫后，不得再次拨打或变相骚扰。",
            },
            "fake_commit": {
                "clause_no": "红线手册-4.3",
                "clause_text": "禁止未经授权向债务人承诺减免、延期或撤销记录。",
            },
            "legal_overstate": {
                "clause_no": "红线手册-4.4",
                "clause_text": "禁止夸大法律后果（起诉、拘留、征信黑名单等）误导、胁迫债务人。",
            },
            "unverified": {
                "clause_no": "红线手册-5.1",
                "clause_text": "关键事实（金额、账期、身份）未经核实不得向债务人断言。",
            },
            "data_fabrication": {
                "clause_no": "红线手册-5.2",
                "clause_text": "禁止伪造、篡改业务数据或捏造还款事实。",
            },
            "privacy_leak": {
                "clause_no": "红线手册-6.1",
                "clause_text": "禁止向无关第三方泄露债务人债务、联系方式等隐私信息。",
            },
        },
        "by_severity": {
            "high": {
                "clause_no": "红线手册-1.1",
                "clause_text": "命中高危违禁词，按高危违规处理。",
            },
            "medium": {
                "clause_no": "红线手册-1.2",
                "clause_text": "命中中危违禁词，按中危违规处理。",
            },
            "low": {
                "clause_no": "红线手册-1.3",
                "clause_text": "命中低危违禁词，按低危违规处理。",
            },
        },
    },
}


def _rule_entry(rule_id: str, rule_type: str, severity: str, redline_tag: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {}
    if rule_id == "R14":
        params = {"keywords": list(R14_KEYWORDS)}
    entry: dict[str, Any] = {
        "rule_id": rule_id,
        "type": rule_type,
        "enabled": True,
        "severity": severity,
        "params": params,
    }
    if redline_tag is not None:
        entry["redline_tag"] = redline_tag
    return entry


V1_RULES: list[dict[str, Any]] = [_rule_entry(*row) for row in _RULE_META]

V1_SEED: dict[str, Any] = {
    "version": QC_RULES_VERSION_V1,
    "status": "active",
    "rules": V1_RULES,
    "penalty": PENALTY_V1,
    "same_word_window_ms": SAME_WORD_WINDOW_MS_V1,
    "deduction_policy": DEDUCTION_POLICY_V1,
    "description": "M1 default qc_rules package",
}

"""V2 种子投诉：≥3 类可区分场景（违规 / 误解 / 体验差）。

供灌数脚本与本地真图联调；PII 均为虚构。
API 创建时 `case_id` 会作为 complaint_id（见 ComplaintService.create）。
"""
from __future__ import annotations

import uuid

VERDICT_AGENT_VIOLATION = "agent_violation"
VERDICT_USER_MISUNDERSTANDING = "user_misunderstanding"
VERDICT_COMPLIANT_BUT_POOR = "compliant_but_poor_experience"
VERDICT_PENDING = "pending"

# 场景标签（与 MockMCPClient._detect_scenario 前缀一致）
SCENARIO_AGENT = "v2_agent_violation"
SCENARIO_MISUNDERSTAND = "v2_user_misunderstanding"
SCENARIO_POOR_EXP = "v2_poor_experience"
SCENARIO_PENDING = "v2_pending"

V2_SEED_COMPLAINTS: list[dict] = [
    {
        "case_id": "case-v2-agent-001",
        "complaint_text": (
            "昨晚将近半夜，催收坐席张测试反复来电，用威胁口吻说再不还款就上门找我家人，"
            "还说要把我的欠款情况发给单位同事。我要求立刻停止这种威胁式催收。"
        ),
        "channel": "phone",
        "expected_verdict": VERDICT_AGENT_VIOLATION,
        "scenario": SCENARIO_AGENT,
        "tendency_note": "偏 agent_violation：禁止时段 + 威胁上门/泄露隐私话术",
        "user_id": "U-V2-9001",
        "customer_name_fake": "测*试",
        "phone_fake": "13800009001",
    },
    {
        "case_id": "case-v2-misunderstand-001",
        "complaint_text": (
            "系统显示我已经提前结清本金，为什么还在按日扣利息？我怀疑你们多收了利息，"
            "请核对计息规则并退还多收部分。我不是故意拖欠。"
        ),
        "channel": "app",
        "expected_verdict": VERDICT_USER_MISUNDERSTANDING,
        "scenario": SCENARIO_MISUNDERSTAND,
        "tendency_note": "偏 user_misunderstanding：对结清/计息规则理解偏差",
        "user_id": "U-V2-9002",
        "customer_name_fake": "李*测",
        "phone_fake": "13800009002",
    },
    {
        "case_id": "case-v2-poor-exp-001",
        "complaint_text": (
            "催收电话内容没有明显违规，但坐席语气生硬、重复打断我提问，"
            "让我感觉被敷衍，希望改进服务态度并给出清晰还款指引。"
        ),
        "channel": "web",
        "expected_verdict": VERDICT_COMPLIANT_BUT_POOR,
        "scenario": SCENARIO_POOR_EXP,
        "tendency_note": "偏 compliant_but_poor_experience：流程合规但体验差",
        "user_id": "U-V2-9003",
        "customer_name_fake": "王*假",
        "phone_fake": "13800009003",
    },
    {
        "case_id": "case-v2-pending-001",
        "complaint_text": (
            "对方说打过催收电话，但我没有完整录音，也说不清具体威胁内容；"
            "短信记录不全，希望先补证再判定是否违规。"
        ),
        "channel": "phone",
        "expected_verdict": VERDICT_PENDING,
        "scenario": SCENARIO_PENDING,
        "tendency_note": "偏 pending：证据不足，需补录音/电话/短信",
        "user_id": "U-V2-9004",
        "customer_name_fake": "赵*待",
        "phone_fake": "13800009004",
    },
]


def list_v2_scenarios() -> list[str]:
    return sorted({c["scenario"] for c in V2_SEED_COMPLAINTS})


def unique_demo_case_id(original_case_id: str) -> str:
    """Keep V2 scenario prefix (v2-agent / v2-misunderstand / v2-poor / v2-pending) + unique suffix.

    Mock MCP fixtures match these prefixes; demo seed MUST NOT reuse fixed ids.
    """
    stem = original_case_id.rsplit("-", 1)[0]
    return f"{stem}-{uuid.uuid4().hex}"


def iter_demo_seed_creates() -> list[dict]:
    """演示灌数：3 条 = 违规 / 误解 / pending（V4 验收⑤需 pending 样例）。"""
    wanted = {SCENARIO_AGENT, SCENARIO_MISUNDERSTAND, SCENARIO_PENDING}
    seeds = [s for s in V2_SEED_COMPLAINTS if s["scenario"] in wanted]
    return [
        {
            "complaint_text": seed["complaint_text"],
            "case_id": unique_demo_case_id(seed["case_id"]),
            "channel": seed.get("channel", "phone"),
            "scenario": seed["scenario"],
        }
        for seed in seeds
    ]

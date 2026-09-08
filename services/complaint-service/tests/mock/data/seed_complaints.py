"""种子投诉数据：覆盖 8 种典型场景与四值 verdict。

每条种子包含初始 State 快照字段：complaint_id / user_id / case_ids /
complaint_text / submit_time / source_channel + 预期 verdict。
"""
from __future__ import annotations

from datetime import datetime

# 四值 verdict 枚举（与 PRD §6.1 VerdictType 一致，禁止 normal）
VERDICT_AGENT_VIOLATION = "agent_violation"
VERDICT_USER_MISUNDERSTANDING = "user_misunderstanding"
VERDICT_COMPLIANT_BUT_POOR = "compliant_but_poor_experience"
VERDICT_PENDING = "pending"

SEED_COMPLAINTS = [
    {
        # 场景 1：坐席违规
        "complaint_id": "CP-2026-0001",
        "user_id": "U10001",
        "case_ids": ["C-2026-0001"],
        "complaint_text": "催收人员在晚上 11 点打电话威胁我，说不还钱就上门找我家人。",
        "submit_time": "2026-09-01T10:00:00+08:00",
        "source_channel": "phone",
        "expected_verdict": VERDICT_AGENT_VIOLATION,
    },
    {
        # 场景 2：用户误解
        "complaint_id": "CP-2026-0002",
        "user_id": "U10002",
        "case_ids": ["C-2026-0002"],
        "complaint_text": "你们多收了我利息，我已经还完了怎么还在扣钱？",
        "submit_time": "2026-09-01T11:00:00+08:00",
        "source_channel": "app",
        "expected_verdict": VERDICT_USER_MISUNDERSTANDING,
    },
    {
        # 场景 3：合规但体验差
        "complaint_id": "CP-2026-0003",
        "user_id": "U10003",
        "case_ids": ["C-2026-0003"],
        "complaint_text": "催收人员说话态度很差，虽然没违规但让我很不舒服。",
        "submit_time": "2026-09-01T12:00:00+08:00",
        "source_channel": "web",
        "expected_verdict": VERDICT_COMPLIANT_BUT_POOR,
    },
    {
        # 场景 4：多案件合并（3+ 案件）
        "complaint_id": "CP-2026-0004",
        "user_id": "U10004",
        "case_ids": ["C-2026-0004-A", "C-2026-0004-B", "C-2026-0004-C"],
        "complaint_text": "我有三笔贷款，催收人员每天打好几个电话，还在禁止时段来电。",
        "submit_time": "2026-09-01T13:00:00+08:00",
        "source_channel": "phone",
        "expected_verdict": VERDICT_AGENT_VIOLATION,
    },
    {
        # 场景 5：重复投诉（90 天同 user_id）
        "complaint_id": "CP-2026-0005",
        "user_id": "U10005",
        "case_ids": ["C-2026-0005"],
        "complaint_text": "上次投诉的问题还没解决，又开始催收了，你们到底管不管？",
        "submit_time": "2026-09-01T14:00:00+08:00",
        "source_channel": "app",
        "expected_verdict": VERDICT_USER_MISUNDERSTANDING,
    },
    {
        # 场景 6：ASR 残缺
        "complaint_id": "CP-2026-0006",
        "user_id": "U10006",
        "case_ids": ["C-2026-0006"],
        "complaint_text": "催收电话里说了很多过分的话，我要投诉。",
        "submit_time": "2026-09-01T15:00:00+08:00",
        "source_channel": "phone",
        "expected_verdict": VERDICT_PENDING,
    },
    {
        # 场景 7：证据冲突
        "complaint_id": "CP-2026-0007",
        "user_id": "U10007",
        "case_ids": ["C-2026-0007"],
        "complaint_text": "坐席说我欠 5000，但系统显示我只欠 3000，金额对不上。",
        "submit_time": "2026-09-01T16:00:00+08:00",
        "source_channel": "web",
        "expected_verdict": VERDICT_PENDING,
    },
    {
        # 场景 8：法规引用（RAG 3+）
        "complaint_id": "CP-2026-0008",
        "user_id": "U10008",
        "case_ids": ["C-2026-0008"],
        "complaint_text": "催收人员冒充法院工作人员给我发传票，还泄露了我的隐私给第三方。",
        "submit_time": "2026-09-01T17:00:00+08:00",
        "source_channel": "phone",
        "expected_verdict": VERDICT_AGENT_VIOLATION,
    },
]


def get_complaint_by_id(complaint_id: str) -> dict | None:
    """按 complaint_id 查询种子投诉。"""
    for c in SEED_COMPLAINTS:
        if c["complaint_id"] == complaint_id:
            return c
    return None


def get_complaints_by_verdict(verdict: str) -> list[dict]:
    """按预期 verdict 筛选种子投诉。"""
    return [c for c in SEED_COMPLAINTS if c["expected_verdict"] == verdict]

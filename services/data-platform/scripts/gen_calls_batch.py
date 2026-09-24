"""批量通话生成脚本（batch-traffic-loop tasks 3.1~3.6）。

由 `--seed`（默认 42）驱动 `random.Random(seed)` 生成可重复的差异化通话，
满足 spec ADDED「批量通话生成脚本」与 ADDED「批量快照行预处理」要求：

- 主表字段与 call_records（0001 迁移）完全对齐，agent_id 可空
- 明细与 dialog_turns 对齐，speaker CHECK ('ai','user')，不写 'agent'
- 200 通各不相同：场景骨架 × 话术变体池 × 随机槽位 × 可选分支，SHA-256 去重
- 分布约 60% 干净合规 / 25% 客户侧异常 / 15% 坐席违规
- call_status 约 85% completed，其余 no_answer/failed
- 轮次 2~12 不等，通话时间晚于对应案件 create_time
- 坐席违规场景命中 qc-service 现有规则 R11/R12/R13（rule_engine.py 定义）
- 单事务幂等 upsert（按 session_id 覆盖），重跑不重复
- 末尾批量快照行预处理：对每个 case_id upsert dw.ads_case_loop 行

R 规则命中原理（rule_engine.py）：
- R11(threat/medium)：agent 话术含 "必须还"/"不能不还"/"现在就还" 等 → 命中
- R12(fake_identity/high)：agent 话术含 "我是法院"/"我是公安"/"我是警察" 等 → 命中
- R13(stop_call/high)：user 说停催词（"不要再打"等）后 agent 继续施压（"还钱"等）→ 命中
- R15(data_fabrication)：agent 含"天"的轮中数字≠overdue_days → 误命中；
  本脚本将"逾期{days}天"与"金额{amount}元"拆到不同轮，避免 R15 误命中
- 合规/客户异常轮不出现 R11/R12 触发词；客户异常停催轮中 agent 不用 R13_AGENT_PRESSURE 词

前置：gen_cases_batch.py --count N --seed S 已执行（cases 表有对应行）。

用法（仓库根目录，自动读取根 .env）：
    python services/data-platform/scripts/gen_calls_batch.py --count 200 --seed 42
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import random
import sys
import uuid
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# repo root 定位：从脚本目录向上找含 common/ 包的目录。
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = None
for _candidate in (_SCRIPT_DIR, *_SCRIPT_DIR.parents):
    if (_candidate / "common" / "config" / "settings.py").is_file():
        _REPO_ROOT = _candidate
        break
if _REPO_ROOT is None:
    raise SystemExit("无法定位仓库根目录（未找到 common/config/settings.py）")
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from common.config.settings import settings  # noqa: E402

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# --- uuid5 命名空间（与 gen_cases_batch 同模式，确定性派生）---
_GEN_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://ai_collection.local/ns/gen_cases_batch")
_CALL_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://ai_collection.local/ns/gen_calls_batch")

# --- 场景类型 ---
SCN_COMPLIANT = "compliant"
SCN_ANOMALY = "customer_anomaly"
SCN_VIOLATION = "agent_violation"

# 客户异常子类型
ANOM_DENY = "deny_self"
ANOM_STOP = "stop_call"
ANOM_PAID = "claim_paid"

# 坐席违规子类型（括号内为命中规则）
VIO_IMPERSONATE = "impersonate"       # R12
VIO_THREATEN = "threaten"             # R11
VIO_THREATEN_STOP = "threaten_stop"   # R11 + R13
VIO_INSULT = "insult"                  # R11
VIO_HARASS = "harass"                  # R12 + R11

# ====================================================================
# 话术变体池
# 注意：合规/客户异常轮禁止出现 R11/R12 触发词
# ====================================================================

# --- 开场白（agent 身份核实，含 case_tail 确保每通唯一）---
_OPENINGS = [
    "您好，关于案件{case_tail}，请问是{title}本人吗？",
    "您好，我是逾期处理中心的工作人员，关于案件{case_tail}，请问是{title}吗？",
    "您好，这里关于您{product}（案件{case_tail}）的逾期事宜，请问{title}在吗？",
    "您好，请问{title}方便听电话吗？我这边是逾期处理部门，案件号{case_tail}。",
    "您好，{title}您好，我这边负责处理您的逾期案件{case_tail}。",
    "您好，请问{title}在吗？这里是逾期处理中心，案件{case_tail}。",
]

# --- 用户确认 ---
_USER_CONFIRM = [
    "是我。", "对，是我。", "嗯，是我。", "没错，是我。",
    "是的，请问什么事？", "是我，什么事？",
]

# --- 逾期披露（仅含 days + "天"，不含 amount，避免 R15 误命中）---
_DISCLOSURE_DAYS = [
    "您这边有一笔{product}账单已逾期{days}天了，请问能处理吗？",
    "您的{product}已逾期{days}天，请问何时能安排还款？",
    "系统显示您的{product}已逾期{days}天，建议尽快处理。",
    "您这边{product}逾期{days}天了，请问今天方便处理吗？",
    "目前您的{product}已逾期{days}天，请问什么情况？",
]

# --- 金额披露（含 amount + "元"，不含 "天"，避免 R15 误命中）---
_DISCLOSURE_AMOUNT = [
    "目前欠款{amount}元，请问今天能还款吗？",
    "您这边欠款{amount}元，建议尽快安排还款。",
    "当前欠款金额{amount}元，请问怎么处理？",
    "您需要还款{amount}元，请问今天方便吗？",
]

# --- 用户合规回应 ---
_USER_OK = [
    "好的，我这边尽快处理。", "我知道了，今天先还一部分。",
    "好的，我安排一下，明天还。", "行，我想想办法。",
    "好的，我会处理的。", "明白，我尽快还。",
]
_USER_DELAY = [
    "我最近手头紧，过段时间再说。", "能不能宽限几天？",
    "下个月发工资就还。", "我现在没那么多钱。",
    "能不能分期还款？", "这月底之前能还清。",
]

# --- agent 跟进（合规，不含 R11/R12 词）---
_AGENT_FOLLOWUP = [
    "理解您的困难，但逾期会影响征信，建议尽快处理。",
    "我可以帮您申请分期方案，您看可以吗？",
    "如果能在一周内还清，可以减免部分费用。",
    "您看先还一部分，剩下的我们再协商？",
    "请您理解，逾期时间越长影响越大。",
]

# --- 可选分支（agent + user，不含触发词）---
_AGENT_EXTRA = [
    "另外提醒您，逾期记录会上报征信系统。",
    "您目前的账户状态是{stage}，建议尽快还款避免升级。",
    "如果您有困难，可以申请还款计划。",
    "请问您大概什么时候能处理？",
    "建议您尽快联系客服协商还款方案。",
]
_USER_EXTRA = [
    "好的，我知道了。", "明白了，我会处理的。",
    "行，我想想办法。", "下周吧，下周发工资就还。",
    "好的，我会尽快的。",
]

# --- 结束语（agent，不含触发词）---
_CLOSINGS = [
    "好的，请您尽快处理，有任何问题随时联系我们。再见。",
    "好的，感谢您的配合，祝您生活愉快。再见。",
    "好的，请您尽快还款，再见。",
    "好的，我们会记录您的反馈，请您尽快处理。再见。",
    "好的，感谢配合。再见。",
]

# ====================================================================
# 客户异常变体池
# ====================================================================

_USER_DENY = [
    "不是本人。", "你打错了。", "我不是你要找的人。",
    "这个人不在这里。", "换号码了，你打错了。",
]

# 用户要求停催（含 R13_USER 触发词；但 agent 后续不用 R13_AGENT_PRESSURE 词）
_USER_STOP = [
    "不要再打了。", "别打了，我都说了会还的。",
    "停止致电，我再投诉你们了。", "不要打电话了，我知道了。",
    "你们天天打，我要投诉你们。",
]

_USER_CLAIM_PAID = [
    "我已经还了。", "早就还清了。",
    "这笔已经还了，你们系统没更新吗？", "上个月就还了。",
    "我还了，你们查一下。",
]

# agent 对停催的合规回应（禁止含 "还钱"/"必须还"/"欠款"/"逾期"/"不还就"/"今天还"）
_AGENT_STOP_OK = [
    "好的，我理解您的心情。我们会记录您的反馈，后续会调整联系频率。感谢您的接听。",
    "好的，我们会备注您的请求，减少联络频率。再见。",
    "理解，我们会做相应记录。感谢配合。再见。",
]

# agent 对否认本人的合规回应
_AGENT_DENY_OK = [
    "好的，打扰了，抱歉。再见。",
    "不好意思，打扰了。再见。",
    "好的，抱歉打扰。再见。",
]

# agent 对声称已还的合规回应
_AGENT_PAID_OK = [
    "好的，我这边帮您核实一下还款记录，请稍候。",
    "好的，我们会核实您说的还款情况。再见。",
    "理解，我这边查一下系统，如有问题会再联系您。再见。",
]

# ====================================================================
# 坐席违规变体池（含 R11/R12/R13 触发词）
# ====================================================================

# 冒充法院（R12）
_VIO_IMPERSONATE = [
    "我是法院的工作人员，你这笔欠款已经被立案了，必须尽快处理。",
    "我是法院的，你今天不还就强制执行。",
    "我是法院法官，你的案件已经进入司法程序。",
    "我是法院的，你的账户将被冻结，必须还。",
]

# 威胁施压（R11）
_VIO_THREATEN = [
    "你今天必须还，没有拒绝的权利。",
    "这笔钱你不想还也不行，必须还。",
    "现在就还，不能拖了。",
    "今天必须还，不然后果自负。",
    "没有拒绝的权利，必须还。",
]

# 停催后施压（R13: user 说停催词 → agent 说施压词）
_VIO_USER_STOP = [
    "不要再打了。", "别打了。", "停止致电。",
    "不要打电话了。", "我要投诉你们。",
]
_VIO_AGENT_PRESSURE = [
    "你还钱了吗？欠款必须还的。",
    "逾期不还就走法律程序了，还钱。",
    "你不还就今天还，别拖了。",
    "欠款不能不还，今天就处理。",
]

# 辱骂（含 R11 词确保命中）
_VIO_INSULT = [
    "你怎么回事？不想还也不行，必须还。",
    "你这种人我见多了，必须还，别想赖账。",
    "今天必须还，别以为躲着就行。",
    "不想还也不行，没有拒绝的权利。",
]

# 骚扰第三人（agent 对家属说，含 R12/R11 词）
_VIO_HARASS = [
    "你是他家人吧？我是法院的，他欠款必须还，麻烦转告。",
    "我是法院工作人员，{title}有一笔欠款必须还，请转告他。",
    "你是他家属？他这笔钱今天必须还，不然后果自负。",
]

# ====================================================================
# 辅助函数
# ====================================================================

def derive_title(customer_name: str, gender: str) -> str:
    """由 customer_name（如"张*"）和 gender 派生称呼。"""
    surname = customer_name[0] if customer_name else "先"
    if gender == "M":
        return f"{surname}先生"
    if gender == "F":
        return f"{surname}女士"
    return f"{surname}先生"


def compute_case_ids(seed: int, count: int) -> list[str]:
    """复算 gen_cases_batch 生成的 case_id 列表（uuid5 确定性派生）。"""
    return [
        str(uuid.uuid5(_GEN_NAMESPACE, f"batch-traffic-loop:gen_cases_batch:v1:{seed}:{i}"))
        for i in range(count)
    ]


def assign_scenarios(rng: random.Random, count: int) -> list[str]:
    """按 60/25/15 分配场景，shuffle 后返回。"""
    n_compliant = round(count * 0.60)
    n_anomaly = round(count * 0.25)
    n_violation = count - n_compliant - n_anomaly
    scenarios = [SCN_COMPLIANT] * n_compliant + [SCN_ANOMALY] * n_anomaly + [SCN_VIOLATION] * n_violation
    rng.shuffle(scenarios)
    return scenarios


def assign_call_status(rng: random.Random, count: int, scenarios: list[str]) -> list[str]:
    """按 ~85% completed 分配 call_status；坐席违规全部 completed。"""
    n_completed = round(count * 0.85)
    n_violation = sum(1 for s in scenarios if s == SCN_VIOLATION)
    # 坐席违规全 completed
    remaining_completed = n_completed - n_violation
    non_violation_count = count - n_violation
    non_viol_completed = remaining_completed
    non_viol_non_completed = non_violation_count - non_viol_completed
    # 非违规中 completed/非 completed 混排
    non_viol_statuses = ["completed"] * non_viol_completed
    for _ in range(non_viol_non_completed):
        non_viol_statuses.append(rng.choice(["no_answer", "failed"]))
    rng.shuffle(non_viol_statuses)
    # 组装
    result = []
    nv_idx = 0
    for scn in scenarios:
        if scn == SCN_VIOLATION:
            result.append("completed")
        else:
            result.append(non_viol_statuses[nv_idx])
            nv_idx += 1
    return result


def assign_sub_types(rng: random.Random, scenarios: list[str]) -> list[str]:
    """为每个场景分配子类型。"""
    sub_types = []
    for scn in scenarios:
        if scn == SCN_COMPLIANT:
            sub_types.append("standard")
        elif scn == SCN_ANOMALY:
            sub_types.append(rng.choice([ANOM_DENY, ANOM_STOP, ANOM_PAID]))
        elif scn == SCN_VIOLATION:
            sub_types.append(rng.choice([
                VIO_IMPERSONATE, VIO_THREATEN, VIO_THREATEN_STOP,
                VIO_INSULT, VIO_HARASS,
            ]))
    return sub_types


def format_amount(overdue_amount) -> str:
    """格式化金额为整数字符串（避免 .00 导致 R15 数字提取问题）。"""
    return str(int(float(overdue_amount)))


# ====================================================================
# 对话生成（场景骨架 × 变体池 × 随机槽位 × 可选分支）
# ====================================================================

def _make_turn(turn_number: int, speaker: str, text: str) -> dict:
    return {"turn_number": turn_number, "speaker": speaker, "text": text}


def generate_dialog(
    rng: random.Random, slots: dict, scenario: str, sub_type: str, call_status: str
) -> list[dict]:
    """根据场景骨架 + 变体池 + 随机槽位 + 可选分支生成对话轮次。"""
    # no_answer: 仅 1 轮开场（无用户应答）
    if call_status == "no_answer":
        return [_make_turn(1, "ai", rng.choice(_OPENINGS).format(**slots))]

    # failed: 开场 + 部分应答后断线
    if call_status == "failed":
        turns = [
            _make_turn(1, "ai", rng.choice(_OPENINGS).format(**slots)),
            _make_turn(2, "user", rng.choice(_USER_CONFIRM)),
        ]
        if rng.random() < 0.5:
            turns.append(_make_turn(3, "ai", "您好，这边信号不太好，能听清吗？"))
        return turns

    # completed: 完整对话
    if scenario == SCN_COMPLIANT:
        return _gen_compliant(rng, slots)
    if scenario == SCN_ANOMALY:
        return _gen_anomaly(rng, slots, sub_type)
    if scenario == SCN_VIOLATION:
        return _gen_violation(rng, slots, sub_type)
    return _gen_compliant(rng, slots)


def _gen_compliant(rng: random.Random, slots: dict) -> list[dict]:
    """干净合规对话骨架。"""
    turns = []
    tn = 1
    turns.append(_make_turn(tn, "ai", rng.choice(_OPENINGS).format(**slots))); tn += 1
    turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
    turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
    turns.append(_make_turn(tn, "user", rng.choice(_USER_DELAY if rng.random() < 0.4 else _USER_OK))); tn += 1
    turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_AMOUNT).format(**slots))); tn += 1
    # 如果用户拖延，agent 跟进
    if "手头紧" in turns[-2]["text"] or "宽限" in turns[-2]["text"] or "没那么多" in turns[-2]["text"]:
        turns.append(_make_turn(tn, "ai", rng.choice(_AGENT_FOLLOWUP).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_OK))); tn += 1
    # 可选分支 0~2 轮（限制总轮次 ≤12）
    max_extra = max(0, (12 - tn) // 2 - 1)  # 留 1 轮给 closing
    for _ in range(rng.randint(0, min(2, max_extra))):
        turns.append(_make_turn(tn, "ai", rng.choice(_AGENT_EXTRA).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_EXTRA))); tn += 1
    turns.append(_make_turn(tn, "ai", rng.choice(_CLOSINGS).format(**slots))); tn += 1
    return turns


def _gen_anomaly(rng: random.Random, slots: dict, sub_type: str) -> list[dict]:
    """客户侧异常对话骨架。"""
    turns = []
    tn = 1
    turns.append(_make_turn(tn, "ai", rng.choice(_OPENINGS).format(**slots))); tn += 1

    if sub_type == ANOM_DENY:
        turns.append(_make_turn(tn, "user", rng.choice(_USER_DENY))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_AGENT_DENY_OK))); tn += 1
    elif sub_type == ANOM_STOP:
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_STOP))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_AGENT_STOP_OK))); tn += 1
    elif sub_type == ANOM_PAID:
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CLAIM_PAID))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_AGENT_PAID_OK))); tn += 1

    # 可选 0~2 轮补充（限制总轮次 ≤12）
    max_extra = max(0, (12 - tn) // 2)
    for _ in range(rng.randint(0, min(2, max_extra))):
        turns.append(_make_turn(tn, "user", rng.choice(_USER_EXTRA))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_CLOSINGS).format(**slots))); tn += 1
    return turns


def _gen_violation(rng: random.Random, slots: dict, sub_type: str) -> list[dict]:
    """坐席违规对话骨架（含 R11/R12/R13 触发词）。"""
    turns = []
    tn = 1
    turns.append(_make_turn(tn, "ai", rng.choice(_OPENINGS).format(**slots))); tn += 1

    if sub_type == VIO_IMPERSONATE:
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_DELAY))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_VIO_IMPERSONATE))); tn += 1
        turns.append(_make_turn(tn, "user", "你们怎么能这样…")); tn += 1
    elif sub_type == VIO_THREATEN:
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_DELAY))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_VIO_THREATEN))); tn += 1
        turns.append(_make_turn(tn, "user", "我知道了，我会想办法的。")); tn += 1
    elif sub_type == VIO_THREATEN_STOP:
        # R13: user 说停催词 → agent 施压
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_VIO_USER_STOP))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_VIO_AGENT_PRESSURE))); tn += 1
        turns.append(_make_turn(tn, "user", "你们太过分了。")); tn += 1
    elif sub_type == VIO_INSULT:
        turns.append(_make_turn(tn, "user", rng.choice(_USER_CONFIRM))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_DISCLOSURE_DAYS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", rng.choice(_USER_DELAY))); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_VIO_INSULT))); tn += 1
        turns.append(_make_turn(tn, "user", "你怎么说话的？")); tn += 1
    elif sub_type == VIO_HARASS:
        # 骚扰第三人：用户是家属
        turns.append(_make_turn(tn, "user", "不是，我是他家人。什么事？")); tn += 1
        turns.append(_make_turn(tn, "ai", rng.choice(_VIO_HARASS).format(**slots))); tn += 1
        turns.append(_make_turn(tn, "user", "好的，我会转告他的。")); tn += 1
    return turns


# ====================================================================
# SQL 语句
# ====================================================================

_QUERY_CASES = text("""
    SELECT id::text AS case_id, case_number, customer_name, gender,
           product_type, overdue_amount::text AS overdue_amount,
           overdue_days, account_stage, create_time
    FROM cases
    WHERE id::text = ANY(:case_ids)
    ORDER BY case_number
""")

_UPSERT_CALL = text("""
    INSERT INTO call_records (
        id, session_id, case_id, call_type, call_status,
        start_time, end_time, duration_seconds, result, summary, is_delete
    ) VALUES (
        CAST(:id AS uuid), :session_id, CAST(:case_id AS uuid), :call_type, :call_status,
        :start_time, :end_time, :duration_seconds, :result, :summary, '0'
    )
    ON CONFLICT (session_id) DO UPDATE SET
        case_id         = EXCLUDED.case_id,
        call_type       = EXCLUDED.call_type,
        call_status     = EXCLUDED.call_status,
        start_time      = EXCLUDED.start_time,
        end_time        = EXCLUDED.end_time,
        duration_seconds = EXCLUDED.duration_seconds,
        result          = EXCLUDED.result,
        summary         = EXCLUDED.summary,
        is_delete       = '0',
        update_time     = NOW()
""")

_DELETE_TURNS = text("DELETE FROM dialog_turns WHERE session_id = ANY(:session_ids)")

_INSERT_TURN = text("""
    INSERT INTO dialog_turns (session_id, turn_number, speaker, text)
    VALUES (:session_id, :turn_number, :speaker, :text)
""")

_UPSERT_SNAPSHOT = text("""
    INSERT INTO dw.ads_case_loop (
        case_id, customer_name, overdue_amount, overdue_days,
        session_id, call_ended,
        product_type, repayment_records, create_time
    )
    SELECT DISTINCT ON (c.id)
        c.id::text,
        c.customer_name,
        c.overdue_amount,
        c.overdue_days,
        cr.session_id,
        CASE WHEN cr.call_status = 'completed' THEN '1' ELSE '0' END,
        c.product_type,
        c.repayment_records,
        c.create_time
    FROM cases c
    LEFT JOIN call_records cr ON cr.case_id = c.id AND cr.is_delete = '0'
    WHERE c.id::text = ANY(:case_ids)
    ORDER BY c.id, cr.start_time DESC
    ON CONFLICT (case_id) DO UPDATE SET
        customer_name     = EXCLUDED.customer_name,
        overdue_amount     = EXCLUDED.overdue_amount,
        overdue_days       = EXCLUDED.overdue_days,
        session_id         = EXCLUDED.session_id,
        call_ended         = EXCLUDED.call_ended,
        product_type       = EXCLUDED.product_type,
        repayment_records  = EXCLUDED.repayment_records,
        create_time        = EXCLUDED.create_time
""")

# ====================================================================
# 自检函数
# ====================================================================

# R11/R12/R13 触发词（与 rule_engine.py 一致）
_R11_PHRASES = ("必须还", "不能不还", "现在就还", "今天必须还", "没有拒绝的权利", "不想还也不行")
_R12_PHRASES = ("我是法院", "我是公安", "我是警察", "我是律师", "我是法官", "我是检察院", "官方机构", "催收专员")
_R13_USER = ("不要再打", "别打了", "停止致电", "不要打电话", "投诉", "自杀", "不想活", "拒绝沟通")
_R13_AGENT_PRESSURE = ("还钱", "必须还", "欠款", "逾期", "不还就", "今天还")


def _contains(text: str, needles) -> str | None:
    lowered = (text or "").casefold()
    for n in needles:
        if n.casefold() in lowered:
            return n
    return None


def check_speaker_values(turns_list: list[list[dict]]) -> list[str]:
    """自检：speaker 仅出现 ai/user。"""
    errors = []
    for i, turns in enumerate(turns_list):
        for t in turns:
            if t["speaker"] not in ("ai", "user"):
                errors.append(f"call[{i}] turn {t['turn_number']} speaker={t['speaker']} 非法")
    return errors


def check_hash_unique(calls: list[dict]) -> list[str]:
    """自检：200 通对话内容哈希集合大小 = 通话数。"""
    hashes = [c["content_hash"] for c in calls]
    if len(set(hashes)) != len(hashes):
        from collections import Counter
        dupes = [h for h, cnt in Counter(hashes).items() if cnt > 1]
        return [f"对话内容哈希重复: {dupes[:5]} (共 {len(dupes)} 个重复)"]
    return []


def check_distribution(calls: list[dict], count: int) -> list[str]:
    """自检：分布 60/25/15 ±5pp。"""
    errors = []
    n_compliant = sum(1 for c in calls if c["scenario"] == SCN_COMPLIANT)
    n_anomaly = sum(1 for c in calls if c["scenario"] == SCN_ANOMALY)
    n_violation = sum(1 for c in calls if c["scenario"] == SCN_VIOLATION)
    pct_c = n_compliant / count * 100
    pct_a = n_anomaly / count * 100
    pct_v = n_violation / count * 100
    if not (55 <= pct_c <= 65):
        errors.append(f"合规占比 {pct_c:.1f}% 超出 60%±5pp")
    if not (20 <= pct_a <= 30):
        errors.append(f"客户异常占比 {pct_a:.1f}% 超出 25%±5pp")
    if not (10 <= pct_v <= 20):
        errors.append(f"坐席违规占比 {pct_v:.1f}% 超出 15%±5pp")
    return errors


def check_call_status(calls: list[dict], count: int) -> list[str]:
    """自检：completed ~85%±5pp。"""
    n_completed = sum(1 for c in calls if c["call_status"] == "completed")
    pct = n_completed / count * 100
    if not (80 <= pct <= 90):
        return [f"completed 占比 {pct:.1f}% 超出 85%±5pp"]
    return []


def check_call_time(calls: list[dict]) -> list[str]:
    """自检：通话时间晚于对应案件 create_time。"""
    errors = []
    for c in calls:
        if c["start_time"] <= c["case_create_time"]:
            errors.append(
                f"call[{c['index']}] start_time={c['start_time']} "
                f"<= create_time={c['case_create_time']}"
            )
    return errors


def check_turn_count(calls: list[dict]) -> list[str]:
    """自检：completed 轮次 2~12。"""
    errors = []
    for c in calls:
        if c["call_status"] == "completed":
            n = len(c["turns"])
            if not (2 <= n <= 12):
                errors.append(f"call[{c['index']}] completed 但轮次={n} 不在 2~12")
    return errors


def check_violation_hits(calls: list[dict]) -> list[str]:
    """自检：坐席违规场景命中至少一条 R11/R12/R13。"""
    errors = []
    for c in calls:
        if c["scenario"] != SCN_VIOLATION:
            continue
        hit = False
        agent_texts = [t["text"] for t in c["turns"] if t["speaker"] == "ai"]
        # R11/R12: agent 含触发词
        for at in agent_texts:
            if _contains(at, _R11_PHRASES) or _contains(at, _R12_PHRASES):
                hit = True
                break
        # R13: user 含停催词 + 后续 agent 含施压词
        if not hit:
            triggered = False
            for t in c["turns"]:
                if t["speaker"] == "user" and _contains(t["text"], _R13_USER):
                    triggered = True
                elif triggered and t["speaker"] == "ai" and _contains(t["text"], _R13_AGENT_PRESSURE):
                    hit = True
                    break
        if not hit:
            errors.append(f"call[{c['index']}] sub_type={c['sub_type']} 未命中 R11/R12/R13")
    return errors


def check_no_false_violation(calls: list[dict]) -> list[str]:
    """自检：合规/客户异常不含 R11/R12 触发词（避免误命中）。"""
    errors = []
    for c in calls:
        if c["scenario"] == SCN_VIOLATION:
            continue
        for t in c["turns"]:
            if t["speaker"] != "ai":
                continue
            w = _contains(t["text"], _R11_PHRASES)
            if w:
                errors.append(f"call[{c['index']}] scenario={c['scenario']} agent 轮误含 R11 词 '{w}'")
            w = _contains(t["text"], _R12_PHRASES)
            if w:
                errors.append(f"call[{c['index']}] scenario={c['scenario']} agent 轮误含 R12 词 '{w}'")
    return errors


# ====================================================================
# 主函数
# ====================================================================

async def run(count: int, seed: int) -> None:
    rng = random.Random(seed)

    # 1. 复算 case_ids 并从 DB 读案件数据
    case_ids = compute_case_ids(seed, count)
    engine = create_async_engine(settings.pg_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            rows = (
                await conn.execute(_QUERY_CASES, {"case_ids": case_ids})
            ).mappings().all()
        if len(rows) < count:
            raise SystemExit(
                f"cases 表中 seed={seed} 的案件不足: 期望 {count}, 实际 {len(rows)}。"
                f"请先执行 gen_cases_batch.py --count {count} --seed {seed}"
            )
    finally:
        await engine.dispose()

    case_map = {r["case_id"]: dict(r) for r in rows}

    # 2. 分配场景 + call_status + 子类型
    scenarios = assign_scenarios(rng, count)
    call_statuses = assign_call_status(rng, count, scenarios)
    sub_types = assign_sub_types(rng, scenarios)

    # 3. 生成每通通话
    calls = []
    for i in range(count):
        cid = case_ids[i]
        case = case_map[cid]
        scn = scenarios[i]
        cs = call_statuses[i]
        st = sub_types[i]

        title = derive_title(case["customer_name"], case["gender"])
        # case_number 尾 4 位（每案唯一，嵌入开场白确保 200 通哈希各不相同）
        case_tail = case["case_number"][-4:]
        slots = {
            "title": title,
            "surname": case["customer_name"][0],
            "product": case["product_type"],
            "days": str(case["overdue_days"]),
            "amount": format_amount(case["overdue_amount"]),
            "stage": case["account_stage"],
            "case_tail": case_tail,
        }

        turns = generate_dialog(rng, slots, scn, st, cs)
        content = "\n".join(t["text"] for t in turns)
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # 通话时间：晚于 create_time
        case_ct = case["create_time"]
        start_time = case_ct + timedelta(minutes=rng.randint(10, 72 * 60))
        # duration
        if cs == "completed":
            duration = rng.randint(60, 300)
        elif cs == "no_answer":
            duration = rng.randint(15, 30)
        else:
            duration = rng.randint(10, 60)
        end_time = start_time + timedelta(seconds=duration)

        # result
        if cs == "no_answer":
            result = "no_answer"
        elif cs == "failed":
            result = "lost"
        elif scn == SCN_COMPLIANT:
            result = rng.choice(["ptp", "completed"])
        elif scn == SCN_ANOMALY:
            result = rng.choice(["refused", "completed"])
        else:
            result = rng.choice(["escalated", "completed"])

        # summary
        summary_map = {
            SCN_COMPLIANT: "正常催收通话",
            SCN_ANOMALY: {"deny_self": "客户否认本人", "stop_call": "客户要求停催", "claim_paid": "客户声称已还"},
            SCN_VIOLATION: {
                "impersonate": "坐席冒充法院",
                "threaten": "坐席威胁施压",
                "threaten_stop": "坐席停催后施压",
                "insult": "坐席辱骂",
                "harass": "坐席骚扰第三人",
            },
        }
        if scn == SCN_ANOMALY:
            summary = summary_map[scn].get(st, "客户异常")
        elif scn == SCN_VIOLATION:
            summary = summary_map[scn].get(st, "坐席违规")
        else:
            summary = summary_map[scn]
        if cs == "no_answer":
            summary = "无人接听"
        elif cs == "failed":
            summary = "通话中断"

        call_id = str(uuid.uuid5(_CALL_NAMESPACE, f"batch-traffic-loop:gen_calls_batch:v1:{seed}:{i}"))
        session_id = f"batch-call-{seed:04d}-{i:04d}"

        calls.append({
            "index": i,
            "call_id": call_id,
            "session_id": session_id,
            "case_id": cid,
            "call_type": "ai",
            "call_status": cs,
            "start_time": start_time,
            "end_time": end_time,
            "duration_seconds": duration,
            "result": result,
            "summary": summary,
            "turns": turns,
            "content_hash": content_hash,
            "scenario": scn,
            "sub_type": st,
            "case_create_time": case_ct,
        })

    # 4. 写前自检
    pre_errors = []
    pre_errors += check_speaker_values([c["turns"] for c in calls])
    pre_errors += check_hash_unique(calls)
    if count >= 100:
        pre_errors += check_distribution(calls, count)
        pre_errors += check_call_status(calls, count)
    pre_errors += check_call_time(calls)
    pre_errors += check_turn_count(calls)
    pre_errors += check_violation_hits(calls)
    pre_errors += check_no_false_violation(calls)
    if pre_errors:
        raise SystemExit(
            f"写前自检失败（{len(pre_errors)} 项），不写库：\n  - "
            + "\n  - ".join(pre_errors[:20])
        )

    # 5. 单事务写入：call_records + dialog_turns + 快照预处理
    engine = create_async_engine(settings.pg_dsn, poolclass=NullPool)
    try:
        async with engine.begin() as conn:
            # 5a. upsert call_records
            call_rows = [
                {
                    "id": c["call_id"],
                    "session_id": c["session_id"],
                    "case_id": c["case_id"],
                    "call_type": c["call_type"],
                    "call_status": c["call_status"],
                    "start_time": c["start_time"],
                    "end_time": c["end_time"],
                    "duration_seconds": c["duration_seconds"],
                    "result": c["result"],
                    "summary": c["summary"],
                }
                for c in calls
            ]
            await conn.execute(_UPSERT_CALL, call_rows)

            # 5b. 删除旧 dialog_turns
            session_ids = [c["session_id"] for c in calls]
            await conn.execute(_DELETE_TURNS, {"session_ids": session_ids})

            # 5c. 插入新 dialog_turns
            turn_rows = []
            for c in calls:
                for t in c["turns"]:
                    turn_rows.append({
                        "session_id": c["session_id"],
                        "turn_number": t["turn_number"],
                        "speaker": t["speaker"],
                        "text": t["text"],
                    })
            if turn_rows:
                await conn.execute(_INSERT_TURN, turn_rows)

            # 5d. 快照行预处理（task 3.6）
            await conn.execute(_UPSERT_SNAPSHOT, {"case_ids": case_ids})

            # 5e. 写后自检
            post_stats = (
                await conn.execute(
                    text("""
                        SELECT
                            (SELECT count(*) FROM call_records WHERE session_id = ANY(:sids)) AS call_cnt,
                            (SELECT count(DISTINCT session_id) FROM call_records WHERE session_id = ANY(:sids)) AS call_distinct,
                            (SELECT count(*) FROM dialog_turns WHERE session_id = ANY(:sids)) AS turn_cnt,
                            (SELECT count(*) FROM dw.ads_case_loop WHERE case_id = ANY(:cids)) AS snap_cnt,
                            (SELECT count(DISTINCT case_id) FROM dw.ads_case_loop WHERE case_id = ANY(:cids)) AS snap_distinct
                    """),
                    {"sids": session_ids, "cids": case_ids},
                )
            ).mappings().one()

            # 快照列验证
            snap_check = (
                await conn.execute(
                    text("""
                        SELECT
                            count(*) FILTER (WHERE product_type IS NULL) AS null_product,
                            count(*) FILTER (WHERE repayment_records IS NULL) AS null_repay,
                            count(*) FILTER (WHERE create_time IS NULL) AS null_ctime,
                            count(*) FILTER (WHERE qc_record_id IS NOT NULL) AS nonnull_qc,
                            count(*) FILTER (WHERE complaint_id IS NOT NULL) AS nonnull_comp,
                            count(*) FILTER (WHERE call_ended NOT IN ('0','1')) AS bad_ended,
                            count(*) FILTER (WHERE call_ended IS NULL) AS null_ended
                        FROM dw.ads_case_loop
                        WHERE case_id = ANY(:cids)
                    """),
                    {"cids": case_ids},
                )
            ).mappings().one()

            post_errors = []
            if post_stats["call_cnt"] != count or post_stats["call_distinct"] != count:
                post_errors.append(
                    f"call_records 对账失败: cnt={post_stats['call_cnt']} distinct={post_stats['call_distinct']} expect={count}"
                )
            if post_stats["snap_cnt"] != count or post_stats["snap_distinct"] != count:
                post_errors.append(
                    f"snapshot 对账失败: cnt={post_stats['snap_cnt']} distinct={post_stats['snap_distinct']} expect={count}"
                )
            if post_stats["turn_cnt"] == 0:
                post_errors.append("dialog_turns 为空")
            if snap_check["null_product"] > 0 or snap_check["null_repay"] > 0 or snap_check["null_ctime"] > 0:
                post_errors.append(
                    f"快照新列有 NULL: product={snap_check['null_product']} "
                    f"repay={snap_check['null_repay']} ctime={snap_check['null_ctime']}"
                )
            if snap_check["nonnull_qc"] > 0 or snap_check["nonnull_comp"] > 0:
                post_errors.append(
                    f"质检/投诉列非空: qc={snap_check['nonnull_qc']} comp={snap_check['nonnull_comp']}"
                )
            if snap_check["bad_ended"] > 0 or snap_check["null_ended"] > 0:
                post_errors.append(
                    f"call_ended 非 0/1: bad={snap_check['bad_ended']} null={snap_check['null_ended']}"
                )
            if post_errors:
                raise SystemExit(
                    f"写后自检失败（事务回滚），共 {len(post_errors)} 项：\n  - "
                    + "\n  - ".join(post_errors)
                )
    finally:
        await engine.dispose()

    # 6. 输出日志
    n_compliant = sum(1 for c in calls if c["scenario"] == SCN_COMPLIANT)
    n_anomaly = sum(1 for c in calls if c["scenario"] == SCN_ANOMALY)
    n_violation = sum(1 for c in calls if c["scenario"] == SCN_VIOLATION)
    n_completed = sum(1 for c in calls if c["call_status"] == "completed")
    n_no_answer = sum(1 for c in calls if c["call_status"] == "no_answer")
    n_failed = sum(1 for c in calls if c["call_status"] == "failed")
    hash_set_size = len(set(c["content_hash"] for c in calls))
    total_turns = sum(len(c["turns"]) for c in calls)

    print(f"gen_calls_batch done: seed={seed} count={count}")
    print(f"  call_records -> cnt={post_stats['call_cnt']} distinct={post_stats['call_distinct']}")
    print(f"  dialog_turns -> total_turns={total_turns}")
    print(f"  snapshot     -> cnt={post_stats['snap_cnt']} distinct={post_stats['snap_distinct']}")
    print(f"  场景分布 -> 合规={n_compliant}({n_compliant/count*100:.0f}%) "
          f"客户异常={n_anomaly}({n_anomaly/count*100:.0f}%) "
          f"坐席违规={n_violation}({n_violation/count*100:.0f}%)")
    print(f"  call_status -> completed={n_completed}({n_completed/count*100:.0f}%) "
          f"no_answer={n_no_answer} failed={n_failed}")
    print(f"  哈希去重 -> {hash_set_size}/{count} (unique/total)")
    print(f"  快照新列 -> product_type非空={count - snap_check['null_product']} "
          f"repayment非空={count - snap_check['null_repay']} "
          f"create_time非空={count - snap_check['null_ctime']}")
    print(f"  快照质检/投诉列 -> qc_record_id非空={snap_check['nonnull_qc']} "
          f"complaint_id非空={snap_check['nonnull_comp']} (应为0)")
    print(f"  call_ended -> 非法值={snap_check['bad_ended']} NULL={snap_check['null_ended']} (应均为0)")
    sample = calls[0]
    print(
        f"  sample(无PII) -> session={sample['session_id']} case_id={sample['case_id'][:8]}... "
        f"status={sample['call_status']} scenario={sample['scenario']} "
        f"sub_type={sample['sub_type']} turns={len(sample['turns'])} "
        f"result={sample['result']} summary={sample['summary']}"
    )
    print("  selfcheck -> speaker=OK hash_unique=OK distribution=OK call_status=OK "
          "time_order=OK turn_count=OK violation_hit=OK no_false_violation=OK "
          "snapshot_upsert=OK call_ended_char1=OK")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量通话生成（seed 化、幂等 upsert、场景骨架×变体池）")
    parser.add_argument("--count", type=int, default=200, help="生成条数（默认 200）")
    parser.add_argument("--seed", type=int, default=42, help="随机种子（默认 42，同 seed 可重复）")
    args = parser.parse_args(argv)
    if not (1 <= args.count <= 100000):
        parser.error("--count 须在 1~100000 之间")
    if args.seed < 0:
        parser.error("--seed 须为非负整数")
    return args


if __name__ == "__main__":
    _args = parse_args()
    asyncio.run(run(_args.count, _args.seed))

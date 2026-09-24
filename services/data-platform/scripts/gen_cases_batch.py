"""批量案件生成脚本（batch-traffic-loop tasks 2.1~2.4）。

由 `--seed`（默认 42）驱动 `random.Random(seed)` 生成可重复的差异化案件，
每条案件满足 spec ADDED「批量案件生成脚本」20 字段契约：

    case_id(UUID)/case_number(BATCH-YYYYMMDD-NNNN 唯一)/case_source/
    customer_name/phone/id_card/gender/product_type/principal/overdue_amount/
    overdue_days/interest_rate/loan_date/due_date/overdue_date/
    repayment_records/account_stage/case_status/stop_call_status/create_time

PII（design D3）：
- phone 明文仅存在于内存生成阶段，经 common.security.crypto.encrypt()
  （AES-256-GCM）加密并计算 SHA-256，落 phone_encrypted/phone_hash 后明文即弃；
- id_card 明文仅用于校验位重算自检，仅落 id_card_hash（SHA-256）后明文即弃；
- 日志与持久化均不保留明文手机号/身份证/银行卡（本脚本不生成银行卡号）。

幂等（spec 幂等段）：case_id 由 (seed, index) 经 uuid5 确定性派生，case_number
同理由 seed 派生批次日期，单事务 ON CONFLICT (id) DO UPDATE 覆盖，重跑不产生第二行。

自检（task 2.4）：写入在同一事务内回读，执行手机号正则/身份证明文重算（写前）、
落库行零明文 grep、密文解密回环、count 对账；任一失败 raise，事务整体回滚、
进程退出非零。

前置：alembic upgrade head（>=0014，cases 含 10 新列）。

用法（仓库根目录，自动读取根 .env）：
    python services/data-platform/scripts/gen_cases_batch.py --count 200 --seed 42
"""
from __future__ import annotations

import argparse
import asyncio
import calendar
import hashlib
import json
import random
import re
import sys
import uuid
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

# repo root 定位：从脚本目录向上找含 common/ 包的目录（宿主机与容器均适用）。
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
from common.security.crypto import decrypt, encrypt  # noqa: E402

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# --- 契约常量 ---
PHONE_RE = re.compile(r"^1[3-9]\d{9}$")
# 18 位身份证：6 位地区码 + 8 位出生日期 + 3 位顺序码 + 1 位 ISO 7064 MOD 11-2 校验位
IDCARD_RE = re.compile(r"^\d{17}[\dX]$")
SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")

# GB 11643-1999 附录 A（ISO 7064 MOD 11-2）：前 17 位权重与余数→校验码映射
_ISO7064_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
_ISO7064_CHECK_BY_REMAINDER = ("1", "0", "X", "9", "8", "7", "6", "5", "4", "3", "2")

# 生成器专用 uuid5 命名空间（由固定 URL 确定性派生，禁止随机化）
_GEN_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://ai_collection.local/ns/gen_cases_batch")

# 批次日期锚点：batch_date = 锚点 + (seed % 300) 天；
# 同 seed 重跑 case_number 稳定，不同 seed（0~299）天然不撞号。
# 警告：seed 相差 300 的整数倍会生成相同 case_number 前缀但不同 case_id，
#   触发 cases_case_number_key 唯一约束冲突。run() 内有预检拦截。
_BATCH_DATE_ANCHOR = date(2026, 1, 1)

_REGION_CODES = ("110101", "310104", "440106", "510107", "330106", "420106")
_SURNAMES = (
    "张", "王", "李", "赵", "刘", "陈", "杨", "黄", "周", "吴",
    "徐", "孙", "胡", "朱", "高", "林", "何", "郭", "马", "罗",
)
_CASE_SOURCES = ("APP", "OUTSRC", "WECHAT")
_PRODUCT_TYPES = ("信用贷", "消费分期", "信用卡代偿")
# CHECK: pending/processing/paid/bad_debt/lost；多数案件仍在催
_CASE_STATUSES = ("pending", "processing", "paid", "bad_debt", "lost")
_CASE_STATUS_WEIGHTS = (50, 30, 8, 7, 5)

PRINCIPAL_MIN = Decimal("10000")
PRINCIPAL_MAX = Decimal("500000")
OVERDUE_DAYS_MAX = 360

_UPSERT_CASE = text(
    """
    INSERT INTO cases (
        id, case_number, customer_name,
        phone_encrypted, phone_hash, id_card_hash,
        overdue_amount, overdue_days, account_stage, case_status, create_time,
        case_source, gender, product_type, principal, interest_rate,
        loan_date, due_date, overdue_date, repayment_records,
        stop_call_status, is_delete
    ) VALUES (
        CAST(:case_id AS uuid), :case_number, :customer_name,
        :phone_encrypted, :phone_hash, :id_card_hash,
        :overdue_amount, :overdue_days, :account_stage, :case_status, :create_time,
        :case_source, :gender, :product_type, :principal, :interest_rate,
        :loan_date, :due_date, :overdue_date, CAST(:repayment_records AS jsonb),
        :stop_call_status, '0'
    )
    ON CONFLICT (id) DO UPDATE SET
        case_number       = EXCLUDED.case_number,
        customer_name     = EXCLUDED.customer_name,
        phone_encrypted   = EXCLUDED.phone_encrypted,
        phone_hash        = EXCLUDED.phone_hash,
        id_card_hash      = EXCLUDED.id_card_hash,
        overdue_amount    = EXCLUDED.overdue_amount,
        overdue_days      = EXCLUDED.overdue_days,
        account_stage     = EXCLUDED.account_stage,
        case_status       = EXCLUDED.case_status,
        create_time       = EXCLUDED.create_time,
        case_source       = EXCLUDED.case_source,
        gender            = EXCLUDED.gender,
        product_type      = EXCLUDED.product_type,
        principal         = EXCLUDED.principal,
        interest_rate     = EXCLUDED.interest_rate,
        loan_date         = EXCLUDED.loan_date,
        due_date          = EXCLUDED.due_date,
        overdue_date      = EXCLUDED.overdue_date,
        repayment_records = EXCLUDED.repayment_records,
        stop_call_status  = EXCLUDED.stop_call_status,
        is_delete         = '0',
        update_time       = NOW()
    """
)


def iso7064_check_digit(id17: str) -> str:
    """计算 18 位身份证第 18 位校验码（ISO 7064 MOD 11-2 / GB 11643）。"""
    remainder = sum(int(ch) * w for ch, w in zip(id17, _ISO7064_WEIGHTS)) % 11
    return _ISO7064_CHECK_BY_REMAINDER[remainder]


def idcard_valid(id_card: str) -> bool:
    """重算校验位并核对。"""
    if not IDCARD_RE.match(id_card):
        return False
    return iso7064_check_digit(id_card[:17]) == id_card[17]


def derive_gender(id_card: str) -> str:
    """由第 17 位奇偶派生 M/F；无法派生时 U。"""
    if len(id_card) != 18 or not id_card[:17].isdigit():
        return "U"
    return "M" if int(id_card[16]) % 2 == 1 else "F"


def derive_account_stage(overdue_days: int) -> str:
    """≤30 M0 / 31~60 M1 / 61~90 M2 / >90 M3。"""
    if overdue_days <= 30:
        return "M0"
    if overdue_days <= 60:
        return "M1"
    if overdue_days <= 90:
        return "M2"
    return "M3"


def generate_case(rng: random.Random, seed: int, index: int) -> dict:
    """生成一条案件的 20 字段契约（含内存态明文 phone/id_card）。

    返回 dict 含明文 PII（phone/id_card），仅用于写前自检；
    落库前必须经 to_persist_row() 转换，明文即弃。
    """
    case_id = str(uuid.uuid5(_GEN_NAMESPACE, f"batch-traffic-loop:gen_cases_batch:v1:{seed}:{index}"))
    batch_date = _BATCH_DATE_ANCHOR + timedelta(days=seed % 300)
    case_number = f"BATCH-{batch_date:%Y%m%d}-{index + 1:04d}"

    case_source = rng.choice(_CASE_SOURCES)
    # 姓名落库即脱敏（姓 + *），与快照「张*」口径一致，不存全名
    customer_name = f"{rng.choice(_SURNAMES)}*"

    # --- 手机号：1[3-9] + 9 位 ---
    phone = f"1{rng.randint(3, 9)}{rng.getrandbits(32) % 10 ** 9:09d}"

    # --- 身份证：地区码 + 出生日期 + 顺序码 + ISO 7064 校验位 ---
    region = rng.choice(_REGION_CODES)
    birth_year = batch_date.year - rng.randint(22, 60)
    birth_month = rng.randint(1, 12)
    birth_day = rng.randint(1, calendar.monthrange(birth_year, birth_month)[1])
    seq = rng.randrange(0, 1000)  # 第 17 位 = seq 奇偶 → 性别
    id17 = f"{region}{birth_year:04d}{birth_month:02d}{birth_day:02d}{seq:03d}"
    id_card = id17 + iso7064_check_digit(id17)
    gender = derive_gender(id_card)

    product_type = rng.choice(_PRODUCT_TYPES)
    principal = Decimal(str(round(rng.uniform(float(PRINCIPAL_MIN), float(PRINCIPAL_MAX)), 2)))
    # 日利率 0.02%~0.10%，numeric 保留 6 位小数
    interest_rate = Decimal(str(round(rng.uniform(0.0002, 0.0010), 6)))
    overdue_days = rng.randint(1, OVERDUE_DAYS_MAX)
    account_stage = derive_account_stage(overdue_days)
    # overdue_amount ∈ (0, principal×1.5]，两位小数
    overdue_amount = Decimal(
        str(round(float(principal) * rng.uniform(0.05, 1.5), 2))
    ).quantize(Decimal("0.01"))

    # 日期链：loan < due < overdue(== batch_date) < create_time
    term_days = rng.randint(30, 365)
    loan_date = batch_date - timedelta(days=term_days + overdue_days)
    due_date = loan_date + timedelta(days=term_days)
    overdue_date = due_date + timedelta(days=overdue_days)

    # 还款记录 0~5 条，日期落在 loan_date~due_date 之间
    n_records = rng.randint(0, 5)
    repay_days = sorted(rng.sample(range(term_days), n_records)) if n_records else []
    repayment_records = [
        {
            "date": (loan_date + timedelta(days=d)).isoformat(),
            "amount": round(rng.uniform(100.0, float(principal) / 5), 2),
        }
        for d in repay_days
    ]

    case_status = rng.choices(_CASE_STATUSES, weights=_CASE_STATUS_WEIGHTS, k=1)[0]
    # paid 案件已还清 → overdue_amount=0；bad_debt/lost 仍欠 → 保持 >0
    if case_status == "paid":
        overdue_amount = Decimal("0")
    stop_call_status = "callable"
    # create_time 在逾期发生后 0~2 天内（Asia/Shanghai），保证批次3通话晚于建案
    create_time = datetime.combine(overdue_date, time(9, 0), tzinfo=_SHANGHAI) + timedelta(
        minutes=rng.randint(0, 2 * 24 * 60)
    )

    return {
        "case_id": case_id,
        "case_number": case_number,
        "case_source": case_source,
        "customer_name": customer_name,
        "phone": phone,
        "id_card": id_card,
        "gender": gender,
        "product_type": product_type,
        "principal": principal,
        "overdue_amount": overdue_amount,
        "overdue_days": overdue_days,
        "interest_rate": interest_rate,
        "loan_date": loan_date,
        "due_date": due_date,
        "overdue_date": overdue_date,
        "repayment_records": repayment_records,
        "account_stage": account_stage,
        "case_status": case_status,
        "stop_call_status": stop_call_status,
        "create_time": create_time,
    }


def validate_contract(case: dict) -> None:
    """写前内存态契约校验（含手机号正则与身份证校验位重算）。失败即 raise。"""
    cid = case["case_id"]
    uuid.UUID(cid)  # 非法 UUID 直接抛 ValueError
    if not re.match(r"^BATCH-\d{8}-\d{4,}$", case["case_number"]):
        raise ValueError(f"case_number 格式不符: {case['case_number']}")
    if case["case_source"] not in _CASE_SOURCES:
        raise ValueError(f"{cid} case_source 非法: {case['case_source']}")
    if not PHONE_RE.match(case["phone"]):
        raise ValueError(f"{cid} 手机号正则不符")
    if not idcard_valid(case["id_card"]):
        raise ValueError(f"{cid} 身份证 ISO 7064 MOD-11 校验位重算失败")
    if case["gender"] != derive_gender(case["id_card"]):
        raise ValueError(f"{cid} gender 未由身份证第 17 位派生")
    if case["gender"] not in ("M", "F", "U"):
        raise ValueError(f"{cid} gender 非法: {case['gender']}")
    if case["product_type"] not in _PRODUCT_TYPES:
        raise ValueError(f"{cid} product_type 非法")
    if not (PRINCIPAL_MIN <= case["principal"] <= PRINCIPAL_MAX):
        raise ValueError(f"{cid} principal 越界")
    # paid 状态允许 overdue_amount=0（已还清）；其他状态必须 >0
    if case["case_status"] == "paid":
        if not (Decimal("0") <= case["overdue_amount"] <= case["principal"] * Decimal("1.5")):
            raise ValueError(f"{cid} paid 状态 overdue_amount 越界")
    else:
        if not (Decimal("0") < case["overdue_amount"] <= case["principal"] * Decimal("1.5")):
            raise ValueError(f"{cid} overdue_amount 越界")
    if not (1 <= case["overdue_days"] <= OVERDUE_DAYS_MAX):
        raise ValueError(f"{cid} overdue_days 越界")
    if not (case["loan_date"] < case["due_date"] < case["overdue_date"]):
        raise ValueError(f"{cid} 日期链不满足 loan<due<overdue")
    if case["account_stage"] != derive_account_stage(case["overdue_days"]):
        raise ValueError(f"{cid} account_stage 未由 overdue_days 派生")
    if case["case_status"] not in _CASE_STATUSES:
        raise ValueError(f"{cid} case_status 非法")
    if case["stop_call_status"] != "callable":
        raise ValueError(f"{cid} stop_call_status 非默认值")
    if not (0 <= len(case["repayment_records"]) <= 5):
        raise ValueError(f"{cid} repayment_records 条数越界")
    for rec in case["repayment_records"]:
        rec_date = date.fromisoformat(rec["date"])
        if not (case["loan_date"] <= rec_date <= case["due_date"]):
            raise ValueError(f"{cid} 还款记录日期越界: {rec['date']}")
    if case["create_time"] <= datetime.fromordinal(
        case["overdue_date"].toordinal()
    ).replace(tzinfo=_SHANGHAI):
        raise ValueError(f"{cid} create_time 不晚于 overdue_date")


def to_persist_row(case: dict) -> dict:
    """内存契约 → 落库行：phone 加密+SHA-256，id_card 仅 SHA-256，明文即弃。"""
    phone = case["phone"]
    id_card = case["id_card"]
    row = {
        "case_id": case["case_id"],
        "case_number": case["case_number"],
        "customer_name": case["customer_name"],
        "phone_encrypted": encrypt(phone),
        "phone_hash": hashlib.sha256(phone.encode("utf-8")).hexdigest(),
        "id_card_hash": hashlib.sha256(id_card.encode("utf-8")).hexdigest(),
        "overdue_amount": case["overdue_amount"],
        "overdue_days": case["overdue_days"],
        "account_stage": case["account_stage"],
        "case_status": case["case_status"],
        "create_time": case["create_time"],
        "case_source": case["case_source"],
        "gender": case["gender"],
        "product_type": case["product_type"],
        "principal": case["principal"],
        "interest_rate": case["interest_rate"],
        "loan_date": case["loan_date"],
        "due_date": case["due_date"],
        "overdue_date": case["overdue_date"],
        "repayment_records": json.dumps(case["repayment_records"], ensure_ascii=False),
        "stop_call_status": case["stop_call_status"],
    }
    return row


async def run(count: int, seed: int) -> None:
    rng = random.Random(seed)
    batch_date = _BATCH_DATE_ANCHOR + timedelta(days=seed % 300)

    # --- 生成 + 写前契约自检（含手机号正则/身份证校验位全数重算）---
    cases = [generate_case(rng, seed, i) for i in range(count)]
    for case in cases:
        validate_contract(case)
    numbers = [c["case_number"] for c in cases]
    if len(set(numbers)) != count:
        raise SystemExit(f"自检失败：case_number 不唯一（{len(set(numbers))}/{count}）")
    rows = [to_persist_row(c) for c in cases]
    # 明文 PII 仅保留在 cases，不参与任何日志输出/落库；事务结束后随进程释放。

    engine = create_async_engine(settings.pg_dsn, poolclass=NullPool)
    try:
        # 单事务：自检失败整体回滚，不留半截数据
        async with engine.begin() as conn:
            # 预检：同 case_number 已存在但 case_id 不同（跨 seed 撞号）→ 明确报错，
            # 而非让 INSERT 撞 cases_case_number_key 唯一约束后抛 cryptic 异常。
            new_ids = [r["case_id"] for r in rows]
            collision = (
                await conn.execute(
                    text(
                        "SELECT case_number FROM cases "
                        "WHERE case_number = ANY(:numbers) "
                        "AND id::text <> ALL(:ids) LIMIT 1"
                    ),
                    {"numbers": [r["case_number"] for r in rows], "ids": new_ids},
                )
            ).scalar()
            if collision:
                raise SystemExit(
                    f"预检拦截：case_number {collision} 已被其他 seed 占用（"
                    f"batch_date={batch_date.isoformat()}, seed%300={seed % 300}）。"
                    f"请更换 seed（避免与已有批次相差 300 的整数倍）。"
                )

            await conn.execute(_UPSERT_CASE, rows)

            # --- 写后自检（同事务回读，只查本次写入的 case_id 集合）---
            new_case_ids = [r["case_id"] for r in rows]
            db_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT id::text AS case_id, case_number, customer_name,
                               phone_encrypted, phone_hash, id_card_hash,
                               overdue_amount::text AS overdue_amount, overdue_days,
                               account_stage, case_status, create_time,
                               case_source, gender, product_type,
                               principal::text AS principal, interest_rate::text AS interest_rate,
                               loan_date::text AS loan_date, due_date::text AS due_date,
                               overdue_date::text AS overdue_date,
                               repayment_records::text AS repayment_records,
                               stop_call_status
                        FROM cases
                        WHERE id::text = ANY(:ids)
                        ORDER BY case_number
                        """
                    ),
                    {"ids": new_case_ids},
                )
            ).mappings().all()

            stats = (
                await conn.execute(
                    text(
                        """
                        SELECT count(*) AS cnt,
                               count(DISTINCT id) AS distinct_ids,
                               count(DISTINCT case_number) AS distinct_numbers
                        FROM cases WHERE id::text = ANY(:ids)
                        """
                    ),
                    {"ids": new_case_ids},
                )
            ).mappings().one()

            # 写后自检放在提交前：任一失败 raise → 本事务整体回滚，进程退出非零。
            persisted_by_id = {r["case_id"]: dict(r) for r in db_rows}
            _post_check(cases, persisted_by_id, stats, count)

        # 事务提交成功：立即清除明文 PII，仅保留日志所需非 PII 字段
        for c in cases:
            c.pop("phone", None)
            c.pop("id_card", None)
    except Exception:
        raise
    finally:
        await engine.dispose()

    # 日志只允许出现非 PII 字段（id/编号/计数/枚举）
    print(f"gen_cases_batch done: seed={seed} count={count} batch_date={batch_date.isoformat()}")
    print(
        f"  rows -> cases(cnt={stats['cnt']}, distinct_id={stats['distinct_ids']}, "
        f"distinct_case_number={stats['distinct_numbers']})"
    )
    stage_dist: dict[str, int] = {}
    source_dist: dict[str, int] = {}
    for c in cases:
        stage_dist[c["account_stage"]] = stage_dist.get(c["account_stage"], 0) + 1
        source_dist[c["case_source"]] = source_dist.get(c["case_source"], 0) + 1
    print(f"  account_stage 分布 -> {dict(sorted(stage_dist.items()))}")
    print(f"  case_source 分布  -> {dict(sorted(source_dist.items()))}")
    sample = cases[0]
    print(
        "  sample(无 PII) -> "
        f"case_id={sample['case_id']} case_number={sample['case_number']} "
        f"source={sample['case_source']} gender={sample['gender']} "
        f"product={sample['product_type']} principal={sample['principal']} "
        f"overdue_days={sample['overdue_days']} stage={sample['account_stage']} "
        f"loan={sample['loan_date']} due={sample['due_date']} overdue={sample['overdue_date']} "
        f"repay_records={len(sample['repayment_records'])} status={sample['case_status']} "
        f"stop_call={sample['stop_call_status']}"
    )
    print("  selfcheck -> phone_regex=OK idcard_iso7064=OK pii_grep=OK count_reconcile=OK")


def _post_check(cases: list[dict], persisted: dict[str, dict], stats, count: int) -> None:
    """写后自检：回环解密、零明文 grep、count 对账。任一失败抛异常（事务回滚/非零退出）。"""
    errors: list[str] = []

    if stats["cnt"] != count or stats["distinct_ids"] != count or stats["distinct_numbers"] != count:
        errors.append(
            f"count 对账失败 cnt={stats['cnt']} distinct_id={stats['distinct_ids']} "
            f"distinct_number={stats['distinct_numbers']} expect={count}"
        )

    for case in cases:
        cid = case["case_id"]
        row = persisted.get(cid)
        if row is None:
            errors.append(f"{cid} 落库缺失")
            continue

        # 哈希形状 + 解密回环（证明手机号契约且落库非明文）
        if not SHA256_HEX_RE.match(row["phone_hash"]):
            errors.append(f"{cid} phone_hash 非 64 位十六进制")
        if not SHA256_HEX_RE.match(row["id_card_hash"]):
            errors.append(f"{cid} id_card_hash 非 64 位十六进制")
        if row["phone_hash"] != hashlib.sha256(case["phone"].encode()).hexdigest():
            errors.append(f"{cid} phone_hash 与明文不一致")
        if row["id_card_hash"] != hashlib.sha256(case["id_card"].encode()).hexdigest():
            errors.append(f"{cid} id_card_hash 与明文不一致")
        try:
            decrypted = decrypt(row["phone_encrypted"])
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{cid} phone_encrypted 解密失败: {exc}")
        else:
            if not PHONE_RE.match(decrypted) or decrypted != case["phone"]:
                errors.append(f"{cid} 解密手机号不满足契约")

        # 零明文（spec Scenario「PII 不留明文」）：按列值整值锚定判定，
        # 即任一列的完整值不得匹配手机号正则或身份证正则。
        # 不对整行拼接 blob 做非锚定子串搜索——phone_hash/id_card_hash 是 64 位
        # 十六进制，n=200 时约 2.5% 的哈希内部会偶然出现 11 位数字子串，非锚定
        # grep 必然误报；整列值不可能是 11 位手机号或 18 位身份证
        # （哈希 64 位、密文 52 位 base64，长度即不符）。
        for col, val in row.items():
            if isinstance(val, str) and (
                PHONE_RE.fullmatch(val) or IDCARD_RE.fullmatch(val)
            ):
                errors.append(f"{cid} 列 {col} 出现整值明文 PII")

        # 20 字段落库值与内存契约对账（非 PII 字段）
        if row["case_number"] != case["case_number"]:
            errors.append(f"{cid} case_number 不一致")
        if row["gender"] != case["gender"]:
            errors.append(f"{cid} gender 不一致")
        if row["account_stage"] != derive_account_stage(case["overdue_days"]):
            errors.append(f"{cid} account_stage 不一致")
        if Decimal(row["principal"]) != case["principal"]:
            errors.append(f"{cid} principal 不一致")
        if Decimal(row["overdue_amount"]) != case["overdue_amount"]:
            errors.append(f"{cid} overdue_amount 不一致")

    if errors:
        raise SystemExit(
            "自检失败（事务回滚，退出非零），共 {} 项：\n  - {}".format(
                len(errors), "\n  - ".join(errors[:20])
            )
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="批量案件生成（seed 化、幂等 upsert、PII 加密/哈希）")
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

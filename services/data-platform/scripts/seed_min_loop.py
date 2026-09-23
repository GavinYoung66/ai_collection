"""data-platform 最小闭环种子脚本（tasks 1.1~1.4）。

向已有三张业务表灌入一条固定假案件 + 一通已结束 AI 通话（含一句命中
qc-service R12（high，fake_identity）/R11（medium，threat）的坐席高危话），
再向 dw.ads_case_loop upsert 同一行的快照空行（6 列有值，质检列/投诉列先空）。

硬约束：
- cases 主键是 id（UUID），表上没有 case_id 列；固定 CASE_ID 同时作为
  call_records.case_id、事件 case_id、投诉 case_id、快照 case_id。
- dialog_turns.speaker 只能是 ai/user（CHECK 约束），坐席高危话用 ai。
- 电话只写假占位（phone_encrypted 非号码、phone_hash 为假串的 64 位十六进制），
  不出现明文手机号/身份证/银行卡。
- 全脚本单事务：中途失败整体回滚，不留半截数据；重复执行只覆盖，不产生第二行。

前置：先执行 `alembic upgrade head`（0013 建 dw.ads_case_loop）。

用法（仓库根目录，自动读取根 .env）：
    python services/data-platform/scripts/seed_min_loop.py
"""
from __future__ import annotations

import asyncio
import hashlib
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

# 允许从仓库根目录或容器内 /app 直接运行：自动定位 repo root。
# 宿主机：services/data-platform/scripts/seed_min_loop.py → parents[3] = repo root
# 容器内：/app/scripts/seed_min_loop.py → parents[1] = /app（PYTHONPATH 已含 /app）
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parents[3] if len(_SCRIPT_DIR.parents) > 3 else _SCRIPT_DIR.parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from common.config.settings import settings  # noqa: E402

_SHANGHAI = ZoneInfo("Asia/Shanghai")

# --- 固定假值（禁止改成真实 PII）---
CASE_ID = "11111111-2222-4333-8444-555555555501"
CALL_RECORD_ID = "22222222-2222-4333-8444-555555555502"
CASE_NUMBER = "MIN-LOOP-001"
CUSTOMER_NAME = "张*"
PHONE_ENCRYPTED = "fake:enc:min-loop-001"  # 假占位，不是真实号码密文
# 假串的 sha256，64 位十六进制；不是任何真实手机号的哈希
PHONE_HASH = hashlib.sha256(b"min-loop-fake-phone-001").hexdigest()
OVERDUE_AMOUNT = Decimal("5280.00")
OVERDUE_DAYS = 45
ACCOUNT_STAGE = "M1"

SESSION_ID = "min-loop-sess-001"
DURATION_SECONDS = 120  # > 0 且 >=10，避免触发 R09 短通话

# speaker 只能 ai|user；第 5 轮 ai 话术含「我是法院」(R12 high/fake_identity)
# 与「必须还」(R11 medium/threat)，保证后续真开检产生 high 告警。
# 刻意不出现数字+元/天（避 R15）、不出现 R14 词表、用户不触发 R13。
_DIALOG_TURNS: list[dict[str, object]] = [
    {"turn_number": 1, "speaker": "ai", "text": "您好，请问是张先生本人吗？"},
    {"turn_number": 2, "speaker": "user", "text": "是我。"},
    {"turn_number": 3, "speaker": "ai", "text": "您这边有一笔账单逾期了，请问今天能安排处理吗？"},
    {"turn_number": 4, "speaker": "user", "text": "我最近手头紧，过段时间再说。"},
    {"turn_number": 5, "speaker": "ai", "text": "我是法院的，你今天必须还钱，不然后果自负。"},
    {"turn_number": 6, "speaker": "user", "text": "知道了，我会想办法的。"},
]

_UPSERT_CASE = text(
    """
    INSERT INTO cases (
        id, case_number, customer_name, phone_encrypted, phone_hash,
        overdue_amount, overdue_days, account_stage
    ) VALUES (
        CAST(:id AS uuid), :case_number, :customer_name, :phone_encrypted, :phone_hash,
        :overdue_amount, :overdue_days, :account_stage
    )
    ON CONFLICT (id) DO UPDATE SET
        case_number     = EXCLUDED.case_number,
        customer_name   = EXCLUDED.customer_name,
        phone_encrypted = EXCLUDED.phone_encrypted,
        phone_hash      = EXCLUDED.phone_hash,
        overdue_amount  = EXCLUDED.overdue_amount,
        overdue_days    = EXCLUDED.overdue_days,
        account_stage   = EXCLUDED.account_stage,
        is_delete       = '0'
    """
)

_UPSERT_CALL = text(
    """
    INSERT INTO call_records (
        id, session_id, case_id, call_type, call_status,
        start_time, end_time, duration_seconds
    ) VALUES (
        CAST(:id AS uuid), :session_id, CAST(:case_id AS uuid), 'ai', 'completed',
        :start_time, :end_time, :duration_seconds
    )
    ON CONFLICT (session_id) DO UPDATE SET
        case_id         = EXCLUDED.case_id,
        call_type       = EXCLUDED.call_type,
        call_status     = EXCLUDED.call_status,
        start_time      = EXCLUDED.start_time,
        end_time        = EXCLUDED.end_time,
        duration_seconds = EXCLUDED.duration_seconds,
        is_delete       = '0'
    """
)

_DELETE_TURNS = text("DELETE FROM dialog_turns WHERE session_id = :session_id")

_INSERT_TURN = text(
    """
    INSERT INTO dialog_turns (session_id, turn_number, speaker, text)
    VALUES (:session_id, :turn_number, :speaker, :text)
    """
)

# 快照空行：只写 6 列；冲突时只覆盖这 6 列，保留已回流的质检列/投诉列。
_UPSERT_SNAPSHOT = text(
    """
    INSERT INTO dw.ads_case_loop (
        case_id, customer_name, overdue_amount, overdue_days, session_id, call_ended
    ) VALUES (
        :case_id, :customer_name, :overdue_amount, :overdue_days, :session_id, '1'
    )
    ON CONFLICT (case_id) DO UPDATE SET
        customer_name  = EXCLUDED.customer_name,
        overdue_amount = EXCLUDED.overdue_amount,
        overdue_days   = EXCLUDED.overdue_days,
        session_id     = EXCLUDED.session_id,
        call_ended     = EXCLUDED.call_ended
    """
)

_VERIFY = text(
    """
    SELECT
        (SELECT count(*) FROM cases WHERE id = CAST(:case_id_uuid AS uuid)) AS cases_cnt,
        (SELECT count(*) FROM call_records WHERE case_id = CAST(:case_id_uuid AS uuid)) AS calls_cnt,
        (SELECT count(*) FROM dialog_turns WHERE session_id = :session_id) AS turns_cnt,
        (SELECT count(*) FROM dw.ads_case_loop WHERE case_id = :case_id_str) AS snapshot_cnt,
        (SELECT call_ended FROM dw.ads_case_loop WHERE case_id = :case_id_str) AS call_ended
    """
)


async def seed() -> None:
    engine = create_async_engine(settings.pg_dsn, poolclass=NullPool)
    try:
        # 全脚本单事务：任一步失败整体回滚，不留半截数据（task 1.4）。
        async with engine.begin() as conn:
            end_time = datetime.now(_SHANGHAI)
            start_time = end_time - timedelta(seconds=DURATION_SECONDS)

            await conn.execute(
                _UPSERT_CASE,
                {
                    "id": CASE_ID,
                    "case_number": CASE_NUMBER,
                    "customer_name": CUSTOMER_NAME,
                    "phone_encrypted": PHONE_ENCRYPTED,
                    "phone_hash": PHONE_HASH,
                    "overdue_amount": OVERDUE_AMOUNT,
                    "overdue_days": OVERDUE_DAYS,
                    "account_stage": ACCOUNT_STAGE},
            )
            await conn.execute(
                _UPSERT_CALL,
                {
                    "id": CALL_RECORD_ID,
                    "session_id": SESSION_ID,
                    "case_id": CASE_ID,
                    "start_time": start_time,
                    "end_time": end_time,
                    "duration_seconds": DURATION_SECONDS,
                },
            )
            # dialog_turns 无 (session_id, turn_number) 唯一约束，按 session 全量重写。
            await conn.execute(_DELETE_TURNS, {"session_id": SESSION_ID})
            await conn.execute(
                _INSERT_TURN,
                [{**turn, "session_id": SESSION_ID} for turn in _DIALOG_TURNS],
            )
            await conn.execute(
                _UPSERT_SNAPSHOT,
                {
                    "case_id": CASE_ID,
                    "customer_name": CUSTOMER_NAME,
                    "overdue_amount": OVERDUE_AMOUNT,
                    "overdue_days": OVERDUE_DAYS,
                    "session_id": SESSION_ID,
                },
            )

            row = (await conn.execute(_VERIFY, {"case_id_uuid": CASE_ID, "case_id_str": CASE_ID, "session_id": SESSION_ID})).mappings().one()
    except Exception:
        raise
    finally:
        await engine.dispose()

    print("seed_min_loop done:")
    print(f"  case_id={CASE_ID} case_number={CASE_NUMBER} customer={CUSTOMER_NAME}")
    print(f"  session_id={SESSION_ID} end_time(Asia/Shanghai)={end_time.isoformat()} duration={DURATION_SECONDS}s")
    print(
        "  rows -> "
        f"cases={row['cases_cnt']} call_records={row['calls_cnt']} "
        f"dialog_turns={row['turns_cnt']} ads_case_loop={row['snapshot_cnt']} "
        f"call_ended={row['call_ended']!r}"
    )
    if not (
        row["cases_cnt"] == 1
        and row["calls_cnt"] == 1
        and row["turns_cnt"] == len(_DIALOG_TURNS)
        and row["snapshot_cnt"] == 1
        and row["call_ended"] == "1"
    ):
        raise SystemExit("种子自检失败：四张表计数或 call_ended 不符合预期，请检查数据。")


if __name__ == "__main__":
    asyncio.run(seed())

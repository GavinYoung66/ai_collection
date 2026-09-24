"""batch-traffic-loop: cases +10 列、dw.ads_case_loop +3 列。

cases 新增 10 列（case_source/gender/product_type/principal/interest_rate/
loan_date/due_date/overdue_date/repayment_records/stop_call_status），
全部 NULL/DEFAULT，不碰现有行；downgrade DROP COLUMN IF EXISTS。
dw.ads_case_loop 新增 3 列（product_type/repayment_records/create_time），
供 business_ticket 桥从快照读真实业务值，不再固定填空串/空列表。

对照 spec:design.md D2、tasks 1.1/1.2；
specs/data-platform/min-loop/spec.md "dw.ads_case_loop 快照表" 17 列表。

Revision ID: 0014
Revises: 0013
Create Date: 2026-09-24

"""
from typing import Sequence, Union

from alembic import op

revision: str = "0014"
down_revision: Union[str, None] = "0013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # === 1.1 public.cases ADD 10 列（全部 NULL/DEFAULT，不碰现有行）===
    op.execute(
        "ALTER TABLE public.cases "
        "ADD COLUMN IF NOT EXISTS case_source text "
        "CHECK (case_source IS NULL OR case_source IN ('APP','OUTSRC','WECHAT'))"
    )
    op.execute(
        "ALTER TABLE public.cases "
        "ADD COLUMN IF NOT EXISTS gender char(1) "
        "CHECK (gender IS NULL OR gender IN ('M','F','U'))"
    )
    op.execute("ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS product_type text")
    op.execute("ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS principal numeric")
    op.execute("ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS interest_rate numeric")
    op.execute("ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS loan_date date")
    op.execute("ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS due_date date")
    op.execute("ALTER TABLE public.cases ADD COLUMN IF NOT EXISTS overdue_date date")
    op.execute(
        "ALTER TABLE public.cases "
        "ADD COLUMN IF NOT EXISTS repayment_records jsonb DEFAULT '[]'::jsonb"
    )
    op.execute(
        "ALTER TABLE public.cases "
        "ADD COLUMN IF NOT EXISTS stop_call_status text DEFAULT 'callable'"
    )

    # === 1.2 dw.ads_case_loop ADD 3 列（NULL/DEFAULT）===
    op.execute(
        "ALTER TABLE dw.ads_case_loop ADD COLUMN IF NOT EXISTS product_type text"
    )
    op.execute(
        "ALTER TABLE dw.ads_case_loop "
        "ADD COLUMN IF NOT EXISTS repayment_records jsonb"
    )
    op.execute(
        "ALTER TABLE dw.ads_case_loop "
        "ADD COLUMN IF NOT EXISTS create_time timestamp with time zone"
    )

    # 列注释
    op.execute(
        "COMMENT ON COLUMN public.cases.case_source IS "
        "'案件来源：APP/OUTSRC/WECHAT'"
    )
    op.execute(
        "COMMENT ON COLUMN public.cases.gender IS "
        "'性别 M/F/U，char(1) 禁止 boolean'"
    )
    op.execute(
        "COMMENT ON COLUMN public.cases.repayment_records IS "
        "'还款记录 JSONB，0~5 条'"
    )
    op.execute(
        "COMMENT ON COLUMN public.cases.stop_call_status IS "
        "'停催状态，默认 callable'"
    )
    op.execute(
        "COMMENT ON COLUMN dw.ads_case_loop.product_type IS "
        "'案件产品类型，对齐 public.cases，供 business_ticket 桥读取'"
    )
    op.execute(
        "COMMENT ON COLUMN dw.ads_case_loop.repayment_records IS "
        "'还款记录 JSONB，对齐 public.cases，供 business_ticket 桥读取'"
    )
    op.execute(
        "COMMENT ON COLUMN dw.ads_case_loop.create_time IS "
        "'案件创建时间，对齐 public.cases，供 business_ticket 桥读取'"
    )


def downgrade() -> None:
    # === 1.2 dw.ads_case_loop DROP 3 列 ===
    op.execute("ALTER TABLE dw.ads_case_loop DROP COLUMN IF EXISTS create_time")
    op.execute("ALTER TABLE dw.ads_case_loop DROP COLUMN IF EXISTS repayment_records")
    op.execute("ALTER TABLE dw.ads_case_loop DROP COLUMN IF EXISTS product_type")

    # === 1.1 public.cases DROP 10 列 ===
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS stop_call_status")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS repayment_records")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS overdue_date")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS due_date")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS loan_date")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS interest_rate")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS principal")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS product_type")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS gender")
    op.execute("ALTER TABLE public.cases DROP COLUMN IF EXISTS case_source")

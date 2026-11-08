"""V2 种子投诉数据（T121 薄转发层）。

模块本体已迁入 app/mock/data/seed_complaints_v2.py；旧导入路径
``from tests.mock.data.seed_complaints_v2 import ...`` 保持零改动兼容。
"""
from app.mock.data.seed_complaints_v2 import (  # noqa: F401
    SCENARIO_AGENT,
    SCENARIO_MISUNDERSTAND,
    SCENARIO_PENDING,
    SCENARIO_POOR_EXP,
    VERDICT_AGENT_VIOLATION,
    VERDICT_COMPLIANT_BUT_POOR,
    VERDICT_PENDING,
    VERDICT_USER_MISUNDERSTANDING,
    V2_SEED_COMPLAINTS,
    iter_demo_seed_creates,
    list_v2_scenarios,
    unique_demo_case_id,
)

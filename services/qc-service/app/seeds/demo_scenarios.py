"""演示三条定型话术。对白取自 data_materials（自律公约 / 合规细则），禁止另编无关场景。"""
from __future__ import annotations

from typing import TypedDict

# 细则 53：外呼接通后先自报身份、机构名称、催收事由；公约 29：身份透明、工号可溯。
# 细则 11：明示催收主体。核身顺序：先确认本人，再披露逾期（避免未核身披露）。
_SCENARIO_A_TURNS: list[tuple[str, str]] = [
    ("ai", "您好，请问是张先生吗？我是催收坐席工号 A1001，来电沟通还款事宜。"),
    ("user", "是我。"),
    ("ai", "感谢确认。您目前有一笔逾期，请通过官方正规还款入口协商处理。"),
]

# 细则 22 / 公约 59：禁止虚假减免、虚假免息；词表样例「可以减免」出自演示材料口径。
_SCENARIO_B_TURNS: list[tuple[str, str]] = [
    ("ai", "您好，请问是李先生吗？我是催收坐席工号 B2002。"),
    ("user", "是我。"),
    ("ai", "现在还款我们可以给你可以减免，全额免息，马上结清就没这件事了。"),
]

# 细则 12 / 公约 30：禁止冒充公检法；细则 10、公约 45：禁止联系无关第三方 / 爆通讯录。
# 「我是法院的」「找你家人」与手册红线及词表样例一致。
_SCENARIO_C_TURNS: list[tuple[str, str]] = [
    ("ai", "我是法院的，过来配合调查。"),
    ("user", "你们不要再打了。"),
    ("ai", "不还就让你妈妈接电话，我去找你家人。"),
]


class DemoTurn(TypedDict):
    speaker: str
    text: str
    turn_number: int


class DemoScenario(TypedDict):
    code: str
    label: str
    customer_name: str
    overdue_amount: str
    overdue_days: int
    turns: list[DemoTurn]


def _turns(rows: list[tuple[str, str]]) -> list[DemoTurn]:
    out: list[DemoTurn] = []
    for index, (speaker, text) in enumerate(rows, start=1):
        if speaker not in {"ai", "user"}:
            raise ValueError(f"demo turn speaker must be ai|user, got {speaker!r}")
        out.append({"speaker": speaker, "text": text, "turn_number": index})
    return out


DEMO_SCENARIOS: tuple[DemoScenario, ...] = (
    {
        "code": "A",
        "label": "合规核身",
        "customer_name": "张三",
        "overdue_amount": "3800.00",
        "overdue_days": 32,
        "turns": _turns(_SCENARIO_A_TURNS),
    },
    {
        "code": "B",
        "label": "中危虚假承诺",
        "customer_name": "李四",
        "overdue_amount": "5600.00",
        "overdue_days": 45,
        "turns": _turns(_SCENARIO_B_TURNS),
    },
    {
        "code": "C",
        "label": "红线冒充公权力",
        "customer_name": "王五",
        "overdue_amount": "9200.00",
        "overdue_days": 61,
        "turns": _turns(_SCENARIO_C_TURNS),
    },
)

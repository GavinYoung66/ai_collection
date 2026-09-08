"""种子案件数据：与 seed_complaints 的 case_ids 对应。"""
from __future__ import annotations

SEED_CASES = {
    "C-2026-0001": {
        "case_id": "C-2026-0001",
        "overdue_amount": 5000.00,
        "overdue_days": 30,
        "repayment_records": [{"date": "2026-07-15", "amount": 1000}],
        "customer_name_masked": "张*",
        "product_type": "personal_loan",
        "create_time": "2026-06-01T00:00:00+08:00",
    },
    "C-2026-0002": {
        "case_id": "C-2026-0002",
        "overdue_amount": 3200.00,
        "overdue_days": 15,
        "repayment_records": [
            {"date": "2026-08-01", "amount": 800},
            {"date": "2026-08-15", "amount": 800},
        ],
        "customer_name_masked": "李*",
        "product_type": "credit_card",
        "create_time": "2026-05-01T00:00:00+08:00",
    },
    "C-2026-0003": {
        "case_id": "C-2026-0003",
        "overdue_amount": 1500.00,
        "overdue_days": 7,
        "repayment_records": [],
        "customer_name_masked": "王*",
        "product_type": "consumer_loan",
        "create_time": "2026-07-01T00:00:00+08:00",
    },
    "C-2026-0004-A": {
        "case_id": "C-2026-0004-A",
        "overdue_amount": 10000.00,
        "overdue_days": 60,
        "repayment_records": [],
        "customer_name_masked": "赵*",
        "product_type": "personal_loan",
        "create_time": "2026-04-01T00:00:00+08:00",
    },
    "C-2026-0004-B": {
        "case_id": "C-2026-0004-B",
        "overdue_amount": 8000.00,
        "overdue_days": 45,
        "repayment_records": [],
        "customer_name_masked": "赵*",
        "product_type": "credit_card",
        "create_time": "2026-05-01T00:00:00+08:00",
    },
    "C-2026-0004-C": {
        "case_id": "C-2026-0004-C",
        "overdue_amount": 5000.00,
        "overdue_days": 20,
        "repayment_records": [],
        "customer_name_masked": "赵*",
        "product_type": "consumer_loan",
        "create_time": "2026-06-01T00:00:00+08:00",
    },
    "C-2026-0005": {
        "case_id": "C-2026-0005",
        "overdue_amount": 2800.00,
        "overdue_days": 25,
        "repayment_records": [{"date": "2026-08-10", "amount": 500}],
        "customer_name_masked": "孙*",
        "product_type": "personal_loan",
        "create_time": "2026-06-15T00:00:00+08:00",
    },
    "C-2026-0006": {
        "case_id": "C-2026-0006",
        "overdue_amount": 6000.00,
        "overdue_days": 40,
        "repayment_records": [],
        "customer_name_masked": "周*",
        "product_type": "credit_card",
        "create_time": "2026-05-15T00:00:00+08:00",
    },
    "C-2026-0007": {
        "case_id": "C-2026-0007",
        "overdue_amount": 3000.00,
        "overdue_days": 18,
        "repayment_records": [{"date": "2026-08-20", "amount": 2000}],
        "customer_name_masked": "吴*",
        "product_type": "consumer_loan",
        "create_time": "2026-06-10T00:00:00+08:00",
    },
    "C-2026-0008": {
        "case_id": "C-2026-0008",
        "overdue_amount": 12000.00,
        "overdue_days": 90,
        "repayment_records": [],
        "customer_name_masked": "郑*",
        "product_type": "personal_loan",
        "create_time": "2026-03-01T00:00:00+08:00",
    },
}


def get_case(case_id: str) -> dict | None:
    return SEED_CASES.get(case_id)

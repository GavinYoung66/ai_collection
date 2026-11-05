"""T63 / 6.2：人工复核条件更新原子迁移 单测。

验收判据（Q-002 口径：本地以 mock repo 并发单测为验收边界）：
- asyncio.gather 两个复核请求（独立内存快照 + 共享 DB 行）：
  恰好一个成功、复核流水仅一条、落定分数一致；
- rowcount=0 路径抛 A2007；
- MUST NOT 引入 SELECT FOR UPDATE（语句形态断言）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from app.models.qc import QcRecord
from app.services.review_service import ReviewService
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException


def _snapshot() -> SimpleNamespace:
    """一个请求的独立内存快照（真实并发下各请求 SELECT 到各自的 ORM 对象）。"""
    return SimpleNamespace(
        id=uuid4(),
        qc_status="PENDING_REVIEW",
        deduction_total=20,
        compliance_score=80,
        qc_result="violation",
        grade=None,
        risk_level="high",
    )


class SharedConditionalRepo:
    """两个请求共享同一 DB 行；条件更新按 DB 行当前状态返回 rowcount。"""

    def __init__(self, db_row: SimpleNamespace):
        self.db_row = db_row
        self.reviews: list[dict] = []
        self.update_calls = 0
        self.session = SimpleNamespace(commit=AsyncMock())

    async def mark_reviewed_conditional(
        self, qc_record_id, *, qc_status, deduction_total, compliance_score, qc_result
    ) -> int:
        self.update_calls += 1
        if self.db_row.qc_status != "PENDING_REVIEW":
            return 0  # 他方已复核：条件不命中
        self.db_row.qc_status = qc_status
        self.db_row.deduction_total = deduction_total
        self.db_row.compliance_score = compliance_score
        self.db_row.qc_result = qc_result
        return 1

    async def insert_review(self, **kwargs):
        self.reviews.append(kwargs)
        return SimpleNamespace(id=uuid4())


def _svc(repo) -> ReviewService:
    publisher = Mock()
    publisher.publish_review_done = AsyncMock()
    return ReviewService(publisher, repo=repo)


class TestReviewConditionalTransition:
    @pytest.mark.asyncio
    async def test_sequential_second_request_gets_a2007(self):
        """同快照序列化场景：首个成功后，DB 行已迁移，第二请求（新快照）抛 A2007。"""
        db_row = SimpleNamespace(
            qc_status="PENDING_REVIEW",
            deduction_total=None,
            compliance_score=None,
            qc_result=None,
        )
        repo = SharedConditionalRepo(db_row)
        svc = _svc(repo)

        first = await svc.submit(_snapshot(), action="CONFIRM")
        assert first["qc_status"] == "REVIEWED"
        assert len(repo.reviews) == 1

        with pytest.raises(BizException):
            await svc.submit(_snapshot(), action="CONFIRM")  # 独立旧快照再复核
        assert len(repo.reviews) == 1, "复核流水必须仅一条"
        assert repo.update_calls == 2

    @pytest.mark.asyncio
    async def test_gather_two_requests_exactly_one_review_row(self):
        """gather 两个复核请求：恰好一个成功、流水一条、分数一致（Q-002 边界）。"""
        db_row = SimpleNamespace(
            qc_status="PENDING_REVIEW",
            deduction_total=None,
            compliance_score=None,
            qc_result=None,
        )
        repo = SharedConditionalRepo(db_row)
        svc = _svc(repo)

        results = await asyncio.gather(
            svc.submit(_snapshot(), action="CONFIRM"),
            svc.submit(_snapshot(), action="CONFIRM"),
            return_exceptions=True,
        )

        ok = [r for r in results if not isinstance(r, BaseException)]
        failed = [r for r in results if isinstance(r, BizException)]
        assert len(ok) == 1, f"恰好一个成功，实际 {len(ok)}"
        assert len(failed) == 1, "另一个必须抛 BizException（A2007）"
        assert len(repo.reviews) == 1, "复核流水仅一条"
        assert db_row.deduction_total == ok[0]["deduction_total"], "落定分数一致"
        assert db_row.qc_status == "REVIEWED"

    @pytest.mark.asyncio
    async def test_dismiss_zeroes_deduction_via_conditional_update(self):
        """DISMISS 归零：条件更新写入的 deduction_total/compliance_score 正确。"""
        db_row = SimpleNamespace(
            qc_status="PENDING_REVIEW",
            deduction_total=None,
            compliance_score=None,
            qc_result=None,
        )
        repo = SharedConditionalRepo(db_row)
        svc = _svc(repo)

        result = await svc.submit(_snapshot(), action="DISMISS")

        assert result["deduction_total"] == 0
        assert db_row.deduction_total == 0
        assert db_row.compliance_score == 100
        assert db_row.qc_result == "compliant"

    def test_no_select_for_update_in_repo(self):
        """语句形态守护：MUST NOT 引入 SELECT FOR UPDATE（design D3.3）。"""
        from pathlib import Path

        src = Path(__file__).resolve().parents[1].joinpath(
            "app/repositories/review_repo.py"
        ).read_text(encoding="utf-8")
        assert "for_update" not in src, "禁止 SELECT FOR UPDATE（事务边界纠缠）"
        assert "update(QcRecord)" in src and "qc_status == \"PENDING_REVIEW\"" in src
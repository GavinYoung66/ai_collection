"""qc_rules 版本与 active 读取。全库仅一版 active。apply 才切库。"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qc import QcRule
from app.seeds.qc_rules_v1 import V1_SEED
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

_PENALTY_KEYS = ("high", "medium", "low")
_GRADE_KEYS = ("EXCELLENT", "PASS", "WARNING", "SERIOUS")


def package_thresholds_valid(rule: QcRule) -> bool:
    penalty = rule.penalty if isinstance(rule.penalty, dict) else None
    if not penalty or any(key not in penalty for key in _PENALTY_KEYS):
        return False
    grades = rule.grade_thresholds if isinstance(rule.grade_thresholds, dict) else None
    if not grades or any(key not in grades for key in _GRADE_KEYS):
        return False
    window = rule.same_word_window_ms
    try:
        if window is None or int(window) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    return True


class RuleRepo:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active(self) -> QcRule | None:
        stmt = select(QcRule).where(QcRule.status == "active", QcRule.is_delete == "0")
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_version(self, version: str) -> QcRule | None:
        stmt = select(QcRule).where(QcRule.version == version, QcRule.is_delete == "0")
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    def v1_seed_payload(self) -> dict:
        return V1_SEED

    async def apply_version(self, version: str) -> tuple[QcRule, str | None]:
        """原子切换。返回 (active行, previous_version)。非法 A2009；事务失败 A2010。"""
        current = await self.get_active()
        target = await self.get_by_version(version)
        if target is None or not package_thresholds_valid(target):
            raise BizException(ErrorCode.A2009)
        if current is not None and current.version == target.version:
            return current, current.version
        prev_version = current.version if current is not None else None
        prev_current_status = current.status if current is not None else None
        prev_target_status = target.status
        prev_effective = target.effective_at
        now = datetime.now(timezone.utc)
        try:
            if current is not None and current is not target:
                current.status = "archived"
            target.status = "active"
            target.effective_at = now
            flush = getattr(self.session, "flush", None)
            if flush is not None:
                await flush()
            commit = getattr(self.session, "commit", None)
            if commit is not None:
                await commit()
        except BizException:
            raise
        except Exception as exc:
            rollback = getattr(self.session, "rollback", None)
            if rollback is not None:
                await rollback()
            if current is not None:
                current.status = prev_current_status
            target.status = prev_target_status
            target.effective_at = prev_effective
            raise BizException(ErrorCode.A2010) from exc
        return target, prev_version

    @staticmethod
    def to_active_payload(row: QcRule) -> dict[str, Any]:
        effective = getattr(row, "effective_at", None)
        return {
            "version": row.version,
            "status": row.status,
            "penalty": dict(row.penalty or {}),
            "same_word_window_ms": row.same_word_window_ms,
            "grade_thresholds": dict(row.grade_thresholds or {}),
            "effective_at": effective.isoformat() if effective is not None else None,
        }

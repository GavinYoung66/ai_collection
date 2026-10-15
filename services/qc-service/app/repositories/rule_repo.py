"""qc_rules 版本与 active 读取。全库仅一版 active。apply 才切库。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.qc import QcRule
from app.seeds.qc_rules_v1 import V1_SEED
from common.errors.codes import ErrorCode
from common.errors.exceptions import BizException

_PENALTY_KEYS = ("high", "medium", "low")
_AC_DEFAULT_KEY = "__ac_default__"


@dataclass(frozen=True)
class RulePackSnapshot:
    """一次质检运行的规则包快照：penalty 与 deduction_policy 来自同一行 active 包。

    单次读取（一次 get_active），打分与落库共用，避免运行中包切换竞态。
    """

    version: str
    penalty: dict[str, int] = field(default_factory=dict)
    deduction_policy: dict[str, Any] = field(default_factory=dict)


def deduction_policy_missing(rule: QcRule) -> list[str]:
    """返回 deduction_policy 未覆盖的 rule_id（含 __ac_default__ 兜底缺位）。空列表=覆盖完整。"""
    rule_ids = [
        r["rule_id"]
        for r in (rule.rules or [])
        if isinstance(r, dict) and r.get("rule_id")
    ]
    policy = rule.deduction_policy if isinstance(rule.deduction_policy, dict) else None
    if not policy:
        return [*rule_ids, _AC_DEFAULT_KEY]
    missing = [rid for rid in rule_ids if rid not in policy]
    if _AC_DEFAULT_KEY not in policy:
        missing.append(_AC_DEFAULT_KEY)
    return missing


def package_thresholds_valid(rule: QcRule) -> bool:
    penalty = rule.penalty if isinstance(rule.penalty, dict) else None
    if not penalty or any(key not in penalty for key in _PENALTY_KEYS):
        return False
    window = rule.same_word_window_ms
    try:
        if window is None or int(window) <= 0:
            return False
    except (TypeError, ValueError):
        return False
    if deduction_policy_missing(rule):
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

    async def get_active_snapshot(self) -> RulePackSnapshot | None:
        """单次读取 active 包，同时取 penalty 与 deduction_policy。无 active 返回 None。"""
        row = await self.get_active()
        if row is None or not isinstance(row.penalty, dict):
            return None
        policy = row.deduction_policy if isinstance(row.deduction_policy, dict) else {}
        return RulePackSnapshot(
            version=row.version,
            penalty=dict(row.penalty),
            deduction_policy=dict(policy),
        )

    async def get_active_penalty(self) -> dict[str, int] | None:
        """active 规则包 penalty（打分/落库单一事实源）。无 active 返回 None。"""
        row = await self.get_active()
        if row is None or not isinstance(row.penalty, dict):
            return None
        return dict(row.penalty)

    async def get_active_deduction_policy(self) -> dict[str, Any] | None:
        """active 规则包 deduction_policy（静态制度条款映射）。无 active 返回 None。"""
        row = await self.get_active()
        if row is None or not isinstance(row.deduction_policy, dict):
            return None
        return dict(row.deduction_policy)

    async def apply_version(self, version: str) -> tuple[QcRule, str | None]:
        """原子切换。返回 (active行, previous_version)。非法 A2009；事务失败 A2010。"""
        current = await self.get_active()
        target = await self.get_by_version(version)
        if target is None:
            raise BizException(ErrorCode.A2009)
        missing = deduction_policy_missing(target)
        if missing:
            raise BizException(
                ErrorCode.A2009,
                f"规则版本非法：deduction_policy 缺条款 {','.join(missing)}，已回退上一版本",
            )
        if not package_thresholds_valid(target):
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
            "deduction_policy": dict(row.deduction_policy or {}),
            "effective_at": effective.isoformat() if effective is not None else None,
        }

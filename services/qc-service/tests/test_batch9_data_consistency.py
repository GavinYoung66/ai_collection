"""批次 9：qc-service 侧（T66/T81/T83/T80/T84）数据一致性与防护 单测。

验收判据：
- 9.1 [T66]：_r08 金额比较 Decimal 归一——20% 整不触发（float 二进制近似曾误触发）；
  Decimal(str(x)) 往返精度；
- 9.6 [T83]：ptp_amount 非法值（"abc"）规则跳过而非抛错（不中断规则链）；
- 9.4 [T81]：_parse_dt 与 time_parse 同源一致；llm_usage._as_utc 的 naive=Shanghai
  语义（原 naive=UTC 解释废弃，消除窗口边界 8 小时错位）；
- 9.3 [T80]：alert ack 幂等——二次 ack 不改变 acked_by/acked_at；
- 9.7 [T84]：subscriber try/finally 关停闭环形态（qc + dp 两服务）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.services.rule_engine import RuleEngine, _parse_dt, _SHANGHAI
from common.utils.time_parse import parse_naive_or_aware


def _session(due, ptp) -> SimpleNamespace:
    return SimpleNamespace(case_snapshot={"due_amount": due, "ptp_amount": ptp})


@pytest.fixture
def engine() -> RuleEngine:
    return RuleEngine()


class TestR08DecimalPrecision:
    def test_exact_20pct_not_triggered(self, engine):
        """due=30.3 / ptp=36.36（20% 整）：Decimal 语义不触发。

        float 二进制近似下 (36.36-30.3)/30.3 曾得出 0.20000000000000007 > 0.2
        而误触发（T66 修复的核心场景）。
        """
        hits, passed = engine._r08(_session(30.3, 36.36), [], {"id": "R08"})
        assert hits == []
        assert passed is False

    def test_decimal_roundtrip_precision(self):
        """Decimal(str(x)) 往返精度（T66 验收口径）。"""
        value = Decimal("12345.67")
        assert Decimal(str(12345.67)) == value
        assert Decimal(str(float(value))) == value

    def test_none_amounts_pass_through(self, engine):
        """due/ptp 缺失：跳过（回归不变）。"""
        hits, passed = engine._r08(_session(None, None), [], {"id": "R08"})
        assert hits == [] and passed is True


class TestR08PtpDefense:
    def test_invalid_ptp_skipped_not_raised(self, engine):
        """ptp_amount="abc"：规则跳过而非抛错（T83 验收）。"""
        hits, passed = engine._r08(_session(100.0, "abc"), [], {"id": "R08"})
        assert hits == [] and passed is True

    def test_non_numeric_ptp_types_all_defended(self, engine):
        """多种非法 ptp 形态（dict/list/bool）均防御跳过。"""
        for bad in ({"x": 1}, ["1"], True):
            hits, passed = engine._r08(_session(100.0, bad), [], {"id": "R08"})
            assert hits == [] and passed is True


class TestParseDtUnified:
    def test_parse_dt_matches_time_parse(self):
        """同一 naive 字符串两处解析结果一致（T81 验收）。"""
        value = "2026-11-05T10:00:00"
        expected = parse_naive_or_aware(
            value, default_tz=ZoneInfo("Asia/Shanghai")
        ).astimezone(ZoneInfo("Asia/Shanghai"))
        assert _parse_dt(value) == expected

    def test_parse_dt_aware_unchanged_moment(self):
        """aware 输入：时刻不变（仅转 Shanghai 时区表示）。"""
        aware = datetime(2026, 11, 5, 2, 0, 0, tzinfo=timezone.utc)
        result = _parse_dt(aware)
        assert result.timestamp() == aware.timestamp()
        assert result.utcoffset().total_seconds() == 8 * 3600

    def test_parse_dt_none_and_empty(self):
        assert _parse_dt(None) is None
        assert _parse_dt("") is None


class TestAsUtcNaiveShanghai:
    def test_naive_interpreted_as_shanghai(self):
        """naive 10:00 按 Shanghai 解释 → UTC 02:00（原 naive=UTC 解释错 8 小时）。"""
        from app.routers.llm_usage import _as_utc

        naive = datetime(2026, 11, 5, 10, 0, 0)
        result = _as_utc(naive)
        assert result.utcoffset().total_seconds() == 0
        assert result.hour == 2, "naive 应按 Asia/Shanghai 解释后转 UTC 表示"

    def test_aware_input_moment_unchanged(self):
        """aware 输入：时刻不变（仅表示转换）。"""
        from app.routers.llm_usage import _as_utc

        aware = datetime(2026, 11, 5, 10, 0, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        assert _as_utc(aware).timestamp() == aware.timestamp()


class TestAlertAckIdempotent:
    def _repo_session(self, rowcount: int) -> SimpleNamespace:
        return SimpleNamespace(
            execute=AsyncMock(return_value=SimpleNamespace(rowcount=rowcount)),
            flush=AsyncMock(),
            refresh=AsyncMock(),
        )

    @pytest.mark.asyncio
    async def test_first_ack_writes_audit_fields(self):
        from app.repositories.alert_repo import AlertRepo

        row = SimpleNamespace(id=uuid4(), status="PENDING", acked_by=None, acked_at=None)
        repo = AlertRepo(self._repo_session(rowcount=1))
        acked_by = uuid4()

        result = await repo.ack(row, acked_by=acked_by)

        assert result.status == "ACKED"
        assert result.acked_by == acked_by
        assert result.acked_at is not None

    @pytest.mark.asyncio
    async def test_second_ack_keeps_audit_fields(self):
        """二次 ack（已 ACKED 快路径）：acked_by/acked_at 不被覆盖（T80 验收）。"""
        from app.repositories.alert_repo import AlertRepo

        acked_by_first = uuid4()
        first_at = datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc)
        row = SimpleNamespace(
            id=uuid4(),
            status="ACKED",
            acked_by=acked_by_first,
            acked_at=first_at,
        )
        repo = AlertRepo(self._repo_session(rowcount=0))

        result = await repo.ack(row, acked_by=uuid4())

        assert result.acked_by == acked_by_first, "审计字段 MUST NOT 被覆盖"
        assert result.acked_at == first_at

    @pytest.mark.asyncio
    async def test_concurrent_window_reads_back_original(self):
        """并发窗口（rowcount=0）：refresh 读回 DB 状态，不覆盖审计字段。"""
        from app.repositories.alert_repo import AlertRepo

        acked_by_first = uuid4()
        row = SimpleNamespace(
            id=uuid4(),
            status="PENDING",  # 内存快照仍 PENDING（他方已先行 ack）
            acked_by=acked_by_first,
            acked_at=datetime(2026, 11, 5, 9, 0, 0, tzinfo=timezone.utc),
        )

        async def _refresh(obj):
            obj.status = "ACKED"  # 模拟 refresh 读回 DB 状态

        session = self._repo_session(rowcount=0)
        session.refresh = AsyncMock(side_effect=_refresh)
        repo = AlertRepo(session)

        result = await repo.ack(row, acked_by=uuid4())

        assert result.acked_by == acked_by_first, "并发窗口下审计字段不覆盖"
        session.execute.assert_awaited_once()


class TestSubscriberClosureForm:
    def test_qc_and_dp_subscribers_use_try_finally(self):
        """T84：qc/dp 两服务的 subscribe 循环均纳入 try/finally，aclose 在 finally。"""
        import configparser  # noqa: F401 — 占位防误删 import 段
        from pathlib import Path

        qc_src = (
            Path(__file__).resolve().parents[1]
            / "app/events/subscriber.py"
        ).read_text(encoding="utf-8")
        dp_src = (
            Path(__file__).resolve().parents[2]
            / "data-platform/app/events/subscriber.py"
        ).read_text(encoding="utf-8")
        for name, src in (("qc", qc_src), ("dp", dp_src)):
            fidx = src.find("T84：subscribe 循环纳入 try/finally")
            assert fidx > 0, f"{name} subscriber 缺 T84 注释标记"
            tail = src[fidx:]
            assert "finally:" in tail, f"{name} subscriber 缺 finally 关停"
            fpos = tail.find("finally:")
            assert "aclose" in tail[fpos : fpos + 200], (
                f"{name} subscriber 的 finally 中应幂等 aclose"
            )


class TestR08R15NonFiniteSkip:
    """批次修复 W1/W7：NaN/Infinity 等非有限值视为缺失跳过。

    Decimal("nan") 解析合法但比较抛 InvalidOperation 中断规则链；
    Decimal("Infinity") 在 _r08 恒触发 20% 偏差误命中、在 _r15 的
    int(float(x)) 处抛 OverflowError——均须按缺失跳过而非抛错。
    """

    @pytest.mark.parametrize(
        "due,ptp",
        [
            (float("nan"), 100.0),
            (30.3, float("nan")),
            (float("inf"), 36.36),
            (30.3, float("-inf")),
        ],
    )
    def test_r08_nonfinite_skipped(self, engine, due, ptp):
        hits, passed = engine._r08(_session(due, ptp), [], {"id": "R08"})
        assert hits == [] and passed is True

    def test_r15_nonfinite_due_skipped(self, engine):
        """due=NaN：_norm_int 返回 None，不命中、不抛错。"""
        session = SimpleNamespace(
            case_snapshot={"due_amount": float("nan"), "overdue_days": None}
        )
        turns = [{"speaker": "agent", "text": "欠款 500 元，逾期 30 天"}]
        hits, passed = engine._r15(session, turns, {"id": "R15"})
        assert hits == [] and passed is False

    def test_r15_nonfinite_days_skipped(self, engine):
        """days=Infinity：_norm_int 返回 None，不命中、不抛错。"""
        session = SimpleNamespace(
            case_snapshot={"due_amount": 3000, "overdue_days": float("inf")}
        )
        turns = [{"speaker": "agent", "text": "逾期 90 天了"}]
        hits, passed = engine._r15(session, turns, {"id": "R15"})
        assert hits == [] and passed is False
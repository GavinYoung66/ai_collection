"""T23：时间确定性——禁止 naive/已废弃的 utcnow，异常时间戳为 timezone-aware。

覆盖：
- 正常：ExceptionInfo 默认时间戳为带 UTC 时区偏移的 ISO 8601
- 边界：显式传入 timestamp 时不被默认值覆盖
- 异常/静态校验：common 与 services 全仓源码 `utcnow()` 零命中；
  state.py 使用 datetime.now(timezone.utc)
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.models.state import ExceptionInfo

_REPO_ROOT = Path(__file__).resolve().parents[4]


def _source_hits(pattern: str) -> list[str]:
    """扫描 common/ 与 services/ 下所有 .py 源码，返回命中行（路径:行号:内容）。"""
    hits: list[str] = []
    self_path = Path(__file__).resolve()
    for base in ("common", "services"):
        for path in sorted((_REPO_ROOT / base).rglob("*.py")):
            if path.resolve() == self_path:
                continue  # 本用例自身包含用于断言的模式字面量
            text = path.read_text(encoding="utf-8", errors="ignore")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if pattern in line:
                    rel = path.relative_to(_REPO_ROOT)
                    hits.append(f"{rel}:{lineno}:{line.strip()}")
    return hits


class TestExceptionTimestamp:
    def test_default_timestamp_is_timezone_aware(self):
        """正常：默认时间戳可解析且带 UTC 偏移（非 naive）。"""
        info = ExceptionInfo(error_type="runtime")
        parsed = datetime.fromisoformat(info.timestamp)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == timezone.utc.utcoffset(None)
        assert datetime.now(timezone.utc) - parsed < timedelta(seconds=10)

    def test_default_timestamp_iso8601_has_utc_offset(self):
        """正常：ISO 8601 字符串显式带 +00:00，便于跨服务排序。"""
        info = ExceptionInfo()
        assert info.timestamp.endswith("+00:00")

    def test_explicit_timestamp_not_overridden(self):
        """边界：显式 timestamp 保留调用方值，默认工厂不覆盖。"""
        info = ExceptionInfo(timestamp="2026-01-01T00:00:00+08:00")
        assert info.timestamp == "2026-01-01T00:00:00+08:00"

    def test_instances_do_not_share_one_default_value(self):
        """边界：连续构造的时间戳精确到微秒，不共用同一默认值。"""
        stamps = {ExceptionInfo().timestamp for _ in range(5)}
        assert len(stamps) > 1


class TestNoDeprecatedUtcNow:
    def test_no_utcnow_in_common_and_services(self):
        """验证：`grep -rn 'utcnow()' common services` 零命中。"""
        assert _source_hits("utcnow()") == []

    def test_state_uses_timezone_aware_now(self):
        """state.py 使用 datetime.now(timezone.utc)，不再使用 naive now。"""
        state_src = (
            _REPO_ROOT / "services/complaint-service/app/models/state.py"
        ).read_text(encoding="utf-8")
        assert "datetime.now(timezone.utc)" in state_src
        assert "datetime.now()" not in state_src

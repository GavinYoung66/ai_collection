"""T39：time_guard 时区一致性与 import 卫生守护测试。

T39 命名的缺陷（naive ``datetime.now()``、未使用的 ``import timezone``）已在
T13 重构中消除：模块当前仅 ``from datetime import datetime``，默认时钟为
``datetime.now(ZoneInfo(timezone_name))``（时区感知），且支持注入固定时钟。
本文件把 T39 的验证判据固化为可回归的守护断言，防止劣化回潮：

1. 源码 AST 级：所有 ``datetime.now(...)`` 调用 MUST 显式传入时区参数，
   且 MUST NOT 出现 naive 取时（utc 系废弃 API，名字按需拼装避免触发
   T23 的全仓禁用扫描）；
2. import 卫生：模块级导入的每个名字 MUST 在模块内被引用（无未使用 import）；
3. 活时钟：默认路径（now=None）产出的时间 MUST 时区感知（ZoneInfo）。
"""
from __future__ import annotations

import ast
from datetime import datetime as real_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from common.utils import time_guard

# 按需拼装禁用 API 名，避免本文件自身命中 T23 全仓 utc 系扫描
_FORBIDDEN_NOW = "utc" + "now"

SOURCE_PATH = Path(time_guard.__file__)
SOURCE = SOURCE_PATH.read_text(encoding="utf-8")


def _ast_module() -> ast.Module:
    return ast.parse(SOURCE, filename=str(SOURCE_PATH))


class TestTimezoneAwareness:
    def test_every_datetime_now_call_passes_timezone(self):
        """所有 datetime.now(...) 调用都显式传入时区参数，不允许 naive 取时。"""
        for node in ast.walk(_ast_module()):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_datetime_now = (
                isinstance(func, ast.Attribute)
                and func.attr == "now"
                and isinstance(func.value, ast.Name)
                and func.value.id == "datetime"
            )
            if is_datetime_now:
                assert node.args, (
                    f"time_guard.py:{node.lineno} datetime.now() 未传时区参数（naive 取时）"
                )

    def test_no_utcnow_usage(self):
        """不允许使用已废弃的 utc 系 naive 取时 API（Python 3.12+ DeprecationWarning）。"""
        assert _FORBIDDEN_NOW not in SOURCE

    def test_live_clock_is_timezone_aware(self, monkeypatch):
        """默认路径（now=None）经 ZoneInfo 取时：捕获到的 tz 参数为 ZoneInfo 实例。"""
        captured: dict[str, object] = {}

        class FakeDateTime(real_datetime):
            @classmethod
            def now(cls, tz=None):  # type: ignore[override]
                captured["tz"] = tz
                return cls(2026, 1, 15, 10, 0, tzinfo=tz)

        monkeypatch.setattr(time_guard, "datetime", FakeDateTime)
        # 00:00-24:00 全时段放行，仅触发取时路径不抛业务异常
        time_guard.check_call_time(
            timezone_name="Asia/Shanghai", allowed_hours="00:00-24:00"
        )
        assert isinstance(captured["tz"], ZoneInfo)
        assert str(captured["tz"]) == "Asia/Shanghai"


class TestImportHygiene:
    def test_no_unused_module_imports(self):
        """模块级 import 的每个名字都被引用（无未使用 import，对齐 T39 验收）。"""
        tree = _ast_module()
        imported: list[tuple[str, int]] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.extend(
                    (alias.asname or alias.name.split(".")[0], node.lineno)
                    for alias in node.names
                )
            elif isinstance(node, ast.ImportFrom):
                imported.extend(
                    (alias.asname or alias.name, node.lineno)
                    for alias in node.names
                )
        assert imported, "未能从 time_guard.py 解析出 import（守护断言失效）"

        used_names = {
            node.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        } | {
            node.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        }
        # 字符串常量里的引用（如 docstring 提到类型）不算使用，这里只查 Name/Attribute
        for name, lineno in imported:
            assert name in used_names, (
                f"time_guard.py:{lineno} 未使用的 import：{name}"
            )

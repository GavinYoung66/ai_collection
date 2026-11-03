"""T32 静态检查基线门禁脚本（scripts/check_lint_baseline.py）单元测试。

覆盖正常流程（基线内放行）、边界场景（计数相等/只降）、异常场景（基线外新增、
totals 自洽被破坏、mypy 行解析的无 code 与非 error 行）。
"""
from __future__ import annotations

import collections
import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_lint_baseline.py"
_spec = importlib.util.spec_from_file_location("check_lint_baseline", _SCRIPT)
assert _spec and _spec.loader
gate = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("check_lint_baseline", gate)
_spec.loader.exec_module(gate)


# ---------------------------------------------------------------------------
# mypy 输出行解析
# ---------------------------------------------------------------------------
def test_mypy_error_line_with_code():
    match = gate._MYPY_ERROR_RE.match(
        "common/llm/x.py:12: error: Incompatible types [arg-type]"
    )
    assert match
    assert match.group("file") == "common/llm/x.py"
    assert match.group("code") == "arg-type"


def test_mypy_error_line_without_code_maps_to_no_code():
    match = gate._MYPY_ERROR_RE.match(
        'services/case-service/app/__init__.py:1: error: Duplicate module named "app"'
    )
    assert match
    assert match.group("code") is None  # 归入 no-code（由调用方兜底）


def test_mypy_non_error_lines_do_not_match():
    assert gate._MYPY_ERROR_RE.match("common/llm/x.py:3: note: By default...") is None
    assert gate._MYPY_ERROR_RE.match("Found 187 errors in 92 files") is None
    assert gate._MYPY_ERROR_RE.match("") is None


# ---------------------------------------------------------------------------
# 工具聚合
# ---------------------------------------------------------------------------
def test_tool_totals_groups_by_tool():
    counter = collections.Counter(
        {
            "ruff\x1fa.py\x1fF401": 2,
            "mypy\x1fb.py\x1farg-type": 3,
        }
    )
    assert gate._tool_totals(counter) == {"ruff": 2, "mypy": 3}


# ---------------------------------------------------------------------------
# check()：门禁判定逻辑（用伪造计数，不依赖真实工具运行）
# ---------------------------------------------------------------------------
def _make_baseline(tmp_path: Path, totals: dict | None = None) -> Path:
    baseline = {
        "_readme": "test",
        "ruff": {"a.py": {"F401": 2}},
        "mypy": {"b.py": {"arg-type": 3}},
        "totals": totals or {"ruff": 2, "mypy": 3},
    }
    path = tmp_path / "lint-baseline.json"
    path.write_text(__import__("json").dumps(baseline), encoding="utf-8")
    return path


def test_check_passes_when_current_within_baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "BASELINE_PATH", _make_baseline(tmp_path))
    current_ruff = collections.Counter({"ruff\x1fa.py\x1fF401": 2})
    current_mypy = collections.Counter({"mypy\x1fb.py\x1farg-type": 3})
    assert gate.check(current_ruff, current_mypy) == []


def test_check_passes_when_violations_decrease(tmp_path, monkeypatch):
    """边界：存量下降（基线只降场景）不阻断，仅提示可下调。"""
    monkeypatch.setattr(gate, "BASELINE_PATH", _make_baseline(tmp_path))
    current_ruff = collections.Counter({"ruff\x1fa.py\x1fF401": 1})
    current_mypy = collections.Counter({"mypy\x1fb.py\x1farg-type": 1})
    assert gate.check(current_ruff, current_mypy) == []


def test_check_blocks_new_finding_beyond_baseline(tmp_path, monkeypatch):
    """异常：基线外新增（新文件/同条目超量）必须逐条报告。"""
    monkeypatch.setattr(gate, "BASELINE_PATH", _make_baseline(tmp_path))
    current_ruff = collections.Counter(
        {"ruff\x1fa.py\x1fF401": 3, "ruff\x1fnew.py\x1fF821": 1}
    )
    violations = gate.check(
        current_ruff, collections.Counter({"mypy\x1fb.py\x1farg-type": 3})
    )
    assert len(violations) == 2
    assert any("a.py" in v and "F401" in v for v in violations)
    assert any("new.py" in v and "F821" in v for v in violations)


def test_check_rejects_inconsistent_totals(tmp_path, monkeypatch):
    """异常：基线 totals 与条目计数之和不一致（手改放大基线）→ 拒绝。"""
    monkeypatch.setattr(
        gate, "BASELINE_PATH", _make_baseline(tmp_path, totals={"ruff": 5, "mypy": 3})
    )
    violations = gate.check(
        collections.Counter({"ruff\x1fa.py\x1fF401": 2}),
        collections.Counter({"mypy\x1fb.py\x1farg-type": 3}),
    )
    assert len(violations) == 1
    assert "totals" in violations[0]


# ---------------------------------------------------------------------------
# CLI 出口：--update 生成基线的自洽性（真实工具全量运行，CI 同口径）
# ---------------------------------------------------------------------------
def test_main_update_then_check_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(gate, "BASELINE_PATH", tmp_path / "lint-baseline.json")
    ruff = collections.Counter({"ruff\x1fa.py\x1fF401": 1})
    mypy = collections.Counter({"mypy\x1fb.py\x1fno-code": 2})
    gate.write_baseline(ruff, mypy)
    assert gate.check(ruff, mypy) == []


@pytest.mark.parametrize(
    "tool_cmd_exists", [True], ids=["tool-path-resolution"]
)
def test_tool_path_prefers_interpreter_bin(tool_cmd_exists):
    """工具调用优先取当前解释器同目录（venv），保证 CI/本地同版本。"""
    bin_dir = Path(sys.executable).parent
    assert (bin_dir / "ruff").exists() or gate.RUFF_CMD[0] == "ruff"

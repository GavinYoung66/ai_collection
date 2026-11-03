#!/usr/bin/env python3
"""T32 静态检查基线门禁：ruff + mypy 存量冻结，基线外新增即失败。

口径（openspec audit-findings-remediation T32 / ci-matrix lint 按需启用 spec）：
- ruff 与 mypy 对仓库当前状态全量执行；存量告警冻结在 ``lint-baseline.json``
  （按 ``文件×规则`` 计数），**基线外新增一条即 exit 1 阻断合并**；
- 基线只降不升：清理存量后运行 ``--update`` 重新生成基线并随修复提交（计数必须
  只减不增，评审把关）；``totals`` 自洽校验防止手改基线文件绕过一致性；
- 检查结果确定性依赖工具版本与依赖环境：CI 与本地必须使用同一 ruff/mypy 版本
  （requirements.lock 钉定）与同一依赖清单安装。

用法：
    python scripts/check_lint_baseline.py            # 门禁检查（CI 用）
    python scripts/check_lint_baseline.py --update   # 重新生成基线（修复存量后用）
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASELINE_PATH = ROOT / "lint-baseline.json"

RUFF_CMD = [str(Path(sys.executable).parent / "ruff"), "check", ".", "--output-format", "json"]

# mypy 按目录分次执行：各服务 app 为独立同名顶层包，单次全仓运行会报
# Duplicate module named "app"。列表新增服务时必须同步 --update 基线。
MYPY_SCOPES = [
    "common",
    "services/call-service/app",
    "services/case-service/app",
    "services/complaint-service/app",
    "services/data-platform/app",
    "services/mcp-gateway/app",
    "services/qc-service/app",
    "services/rag-service/app",
    "services/review-service/app",
]

# ruff 0.1.11 的 JSON 字段名即为 "code"；mypy 部分错误无 code（如 duplicate module）
_NO_CODE = "no-code"


def _rel(path: str) -> str:
    """工具输出的路径归一为仓库相对路径，保证基线跨环境稳定。"""
    p = Path(path)
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


def _run(cmd: list[str]) -> str:
    proc = subprocess.run(
        cmd, cwd=ROOT, capture_output=True, text=True, check=False
    )
    if proc.returncode not in (0, 1):
        # ruff/mypy 以 1 表示"有告警"，其余退出码属执行故障（配置错误等），直接失败
        sys.stderr.write(proc.stdout + proc.stderr)
        raise SystemExit(f"command failed ({' '.join(cmd)}): exit {proc.returncode}")
    return proc.stdout


def collect_ruff() -> collections.Counter[str]:
    findings = json.loads(_run(RUFF_CMD) or "[]")
    counter: collections.Counter[str] = collections.Counter()
    for item in findings:
        key = f"ruff\x1f{_rel(item['filename'])}\x1f{item.get('code') or _NO_CODE}"
        counter[key] += 1
    return counter


def _mypy_cmd(scope: str) -> list[str]:
    mypy = Path(sys.executable).parent / "mypy"
    return [str(mypy), scope, "--no-error-summary", "--no-pretty"]

# mypy 人读输出行：`path:line: error: message [error-code]`；部分错误无 [code] 尾注
_MYPY_ERROR_RE = re.compile(
    r"^(?P<file>[^:]+):(?P<line>\d+): error: (?P<msg>.*?)(?:\s+\[(?P<code>[\w-]+)\])?$"
)


def collect_mypy() -> collections.Counter[str]:
    counter: collections.Counter[str] = collections.Counter()
    for scope in MYPY_SCOPES:
        out = _run(_mypy_cmd(scope))
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            match = _MYPY_ERROR_RE.match(line)
            if not match:
                continue
            # 只统计仓库内文件：site-packages 里的 .pyi 报错（如 numpy stub 与
            # python_version 的语法代差）属环境噪声，不进基线口径
            file_path = Path(match.group("file"))
            if file_path.is_absolute() and ROOT not in file_path.resolve().parents:
                continue
            code = match.group("code") or _NO_CODE
            key = f"mypy\x1f{_rel(match.group('file'))}\x1f{code}"
            counter[key] += 1
    return counter


def _tool_totals(counter: collections.Counter[str]) -> dict[str, int]:
    totals: dict[str, int] = collections.Counter()
    for key, count in counter.items():
        tool = key.split("\x1f", 1)[0]
        totals[tool] += count
    return dict(totals)


def write_baseline(ruff: collections.Counter[str], mypy: collections.Counter[str]) -> None:
    baseline = {
        "_readme": (
            "T32 静态检查基线：按 文件×规则 冻结 ruff/mypy 存量告警计数；"
            "基线只降不升——清理存量后用 scripts/check_lint_baseline.py --update 重新生成；"
            "totals 为自洽校验值，手改条目必须保持 sum(条目计数) == totals"
        ),
        "ruff": {},
        "mypy": {},
        "totals": {},
    }
    for counter, tool in ((ruff, "ruff"), (mypy, "mypy")):
        grouped: dict[str, dict[str, int]] = {}
        for key, count in counter.items():
            _, path, code = key.split("\x1f", 2)
            grouped.setdefault(path, {})[code] = count
        baseline[tool] = dict(sorted(grouped.items()))
    baseline["totals"] = dict(sorted({**_tool_totals(ruff), **_tool_totals(mypy)}.items()))
    BASELINE_PATH.write_text(
        json.dumps(baseline, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"baseline written: {BASELINE_PATH}")
    print(f"totals: {baseline['totals']}")


def check(
    ruff: collections.Counter[str], mypy: collections.Counter[str]
) -> list[str]:
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))

    # 基线自洽校验：条目计数之和必须等于 totals（防手改条目忘记同步 totals）
    for tool, counter in (("ruff", ruff), ("mypy", mypy)):
        declared_sum = sum(
            count for entries in baseline.get(tool, {}).values() for count in entries.values()
        )
        recorded = baseline.get("totals", {}).get(tool)
        if recorded != declared_sum:
            return [
                f"baseline totals[{tool}]={recorded} 与条目计数之和 {declared_sum} 不一致；"
                "基线文件被手工放大或损坏，请用 --update 重新生成并经评审确认"
            ]

    violations: list[str] = []
    for tool, counter in (("ruff", ruff), ("mypy", mypy)):
        frozen = baseline.get(tool, {})
        for key, count in sorted(counter.items()):
            _, path, code = key.split("\x1f", 2)
            allowed = frozen.get(path, {}).get(code, 0)
            if count > allowed:
                violations.append(
                    f"{tool}: {path} [{code}] 新增 {count - allowed} 条"
                    f"（基线 {allowed}，当前 {count}）"
                )
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--update", action="store_true", help="重新生成基线（清理存量后使用，只降不升）"
    )
    args = parser.parse_args()

    ruff = collect_ruff()
    mypy = collect_mypy()

    if args.update:
        write_baseline(ruff, mypy)
        return 0

    violations = check(ruff, mypy)
    totals = _tool_totals(ruff)
    totals.update(_tool_totals(mypy))
    print(f"static findings: ruff={totals.get('ruff', 0)} mypy={totals.get('mypy', 0)}")
    if violations:
        print("\n基线外新增告警（阻断）：")
        for v in violations:
            print(f"  - {v}")
        return 1
    print("lint baseline gate: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Dockerfile 直钉版本与根 requirements.lock 一致性校验（Q-002 裁决方案 A）。

背景：deploy/*/Dockerfile 对个别运行时依赖直钉版本（如 PyJWT），该安装路径
不经过 dependency-audit 的 lock 审计，存在与根 lock 双源漂移的风险。
本脚本强制比对：任何 Dockerfile 内 `name==version` 直钉条目，
若未出现在根 requirements.lock 或版本不一致，即非零退出阻断 CI。

比对规则：
- 包名按 PEP 503 规范化（大小写、-/_ 等价），extras（如 sqlalchemy[asyncio]）剥除后比对；
- 仅校验「钉死 ==」条目，未钉版本的基础包不参与（其版本由 PyPI 解析，无双源漂移面）。

豁免机制：
- Dockerfile 内含 `pins-lock-exempt` 标记注释的文件整体跳过——用于声明
  「服务依赖独立安装」模式（如 deploy/ocr 的 paddle 系，不在根 lock 管辖范围）；
  豁免文件中的 lock 冲突项视为登记在案的待收敛债务，须在部署基线批次处理。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCK_FILE = REPO_ROOT / "requirements.lock"
EXEMPT_MARKER = "pins-lock-exempt"

# 匹配 name 或 name[extras]==version（行内任意位置，容忍引号包裹与反斜杠续行）
PIN_RE = re.compile(
    r"([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?\s*==\s*\\?\"?([A-Za-z0-9.\-]+)",
)
LOCK_PIN_RE = re.compile(
    r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([A-Za-z0-9.\-]+)",
)


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def collect_dockerfile_pins() -> tuple[list[tuple[Path, str, str]], list[str]]:
    """收集 deploy/**/Dockerfile 中的钉死条目；返回 (比对条目, 豁免文件清单)。"""
    pins: list[tuple[Path, str, str]] = []
    exempted: list[str] = []
    for dockerfile in sorted(REPO_ROOT.glob("deploy/**/Dockerfile")):
        text = dockerfile.read_text(encoding="utf-8")
        if EXEMPT_MARKER in text:
            exempted.append(str(dockerfile.relative_to(REPO_ROOT)))
            continue
        for line in text.splitlines():
            for m in PIN_RE.finditer(line):
                pins.append((dockerfile, m.group(1), m.group(2)))
    return pins, exempted


def collect_lock_pins() -> dict[str, str]:
    """根 lock 的钉死条目 → {规范化包名: 版本}。"""
    pins: dict[str, str] = {}
    for line in LOCK_FILE.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        m = LOCK_PIN_RE.match(line)
        if m:
            pins[normalize(m.group(1))] = m.group(2)
    return pins


def main() -> int:
    if not LOCK_FILE.exists():
        print(f"::error::未找到根 {LOCK_FILE.relative_to(REPO_ROOT)}", file=sys.stderr)
        return 1

    lock_pins = collect_lock_pins()
    dockerfile_pins, exempted = collect_dockerfile_pins()
    for rel in exempted:
        print(f"check-dockerfile-pins: {rel} 带 {EXEMPT_MARKER} 标记，跳过（独立安装模式）")
    if not dockerfile_pins:
        print("check-dockerfile-pins: deploy 下无比对条目（0 项）")
        return 0

    errors: list[str] = []
    for dockerfile, name, version in dockerfile_pins:
        rel = dockerfile.relative_to(REPO_ROOT)
        locked = lock_pins.get(normalize(name))
        if locked is None:
            errors.append(
                f"{rel}: {name}=={version} 未出现在根 requirements.lock（禁止 lock 外直钉）"
            )
        elif locked != version:
            errors.append(
                f"{rel}: {name}=={version} 与根 lock 钉版 {locked} 漂移（以 lock 为单一事实源）"
            )

    checked = len(dockerfile_pins)
    if errors:
        print(f"check-dockerfile-pins: {checked} 项比对，{len(errors)} 项不一致：", file=sys.stderr)
        for err in errors:
            print(f"  ::error::{err}", file=sys.stderr)
        return 1

    names = ", ".join(sorted({normalize(n) for _, n, _ in dockerfile_pins}))
    print(f"check-dockerfile-pins: {checked} 项直钉与根 lock 全部一致（{names}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())

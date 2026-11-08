"""事件循环 AST 守护测试（4.2，spec 4.4.2）。

- 规则 1：rag asr_providers/ 与 asr_client.py 的 async def 体内不得出现
  subprocess.run 直接调用（asyncio.to_thread 包裹的独立同步函数体不属于 async 体）
- 规则 2：全服务扫描 async def 函数体内不得出现同步 httpx.get/httpx.post 探活调用
- 规则 3：全服务扫描 async def 函数体内不得出现 time.sleep

扫描范围为生产代码目录（services/*/app 与 common，排除 tests），白名单显式枚举
（文件级：相对路径 + 规则号 + 理由），禁止 # noqa 式静默豁免。
"""
from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# 规则 1 扫描范围（显式枚举文件）
_RULE1_RAG_FILES = [
    "services/rag-service/app/services/asr_client.py",
    *sorted(
        str(p.relative_to(_REPO_ROOT))
        for p in (_REPO_ROOT / "services/rag-service/app/services/asr_providers").glob("*.py")
    ),
]

# 规则 2/3 扫描范围（生产代码目录，排除 tests）
_SCAN_ROOTS = [
    "common",
    "services/qc-service/app",
    "services/complaint-service/app",
    "services/rag-service/app",
    "services/data-platform/app",
    "services/mcp-gateway/app",
]

# 白名单：{(相对路径, 规则号, 行号): 理由}——新增豁免须评审
_WHITELIST: dict[tuple[str, str, int], str] = {}


def _iter_py_files(rel_paths_or_dirs: list[str]) -> list[Path]:
    files: list[Path] = []
    for rel in rel_paths_or_dirs:
        p = _REPO_ROOT / rel
        if p.is_file():
            files.append(p)
        elif p.is_dir():
            files.extend(
                f for f in sorted(p.rglob("*.py")) if "__pycache__" not in f.parts
            )
    return files


def _find_offenders(path: Path, rule: str) -> list[tuple[int, str]]:
    """AST 解析：返回 async def 函数体内命中规则的 (行号, 描述) 列表。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []

    offenders: list[tuple[int, str]] = []

    def matches(call: ast.Call) -> str | None:
        func = call.func
        if not isinstance(func, ast.Attribute):
            return None
        value = func.value
        if not isinstance(value, ast.Name):
            return None
        if rule == "subprocess.run" and value.id == "subprocess" and func.attr == "run":
            return "subprocess.run"
        if rule == "httpx.sync" and value.id == "httpx" and func.attr in {"get", "post"}:
            return f"httpx.{func.attr}"
        if rule == "time.sleep" and value.id == "time" and func.attr == "sleep":
            return "time.sleep"
        return None

    def walk(node: ast.AST, in_async: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.AsyncFunctionDef, ast.FunctionDef)):
                walk(child, in_async or isinstance(child, ast.AsyncFunctionDef))
                continue
            if in_async and isinstance(child, ast.Call):
                hit = matches(child)
                if hit:
                    offenders.append((child.lineno, hit))
            walk(child, in_async)

    walk(tree, False)
    return offenders


class TestRule1AsrSubprocess:
    def test_no_subprocess_run_inside_async_defs(self):
        offenders = []
        for rel in _RULE1_RAG_FILES:
            for lineno, hit in _find_offenders(_REPO_ROOT / rel, "subprocess.run"):
                offenders.append(f"{rel}:{lineno} {hit}")
        assert offenders == [], f"rule1 violated: {offenders}"


class TestRule2SyncHttpxInAsync:
    def test_no_sync_httpx_get_post_inside_async_defs(self):
        offenders = []
        for path in _iter_py_files(_SCAN_ROOTS):
            rel = str(path.relative_to(_REPO_ROOT))
            for lineno, hit in _find_offenders(path, "httpx.sync"):
                if (rel, "httpx.sync", lineno) in _WHITELIST:
                    continue
                offenders.append(f"{rel}:{lineno} {hit}")
        assert offenders == [], f"rule2 violated: {offenders}"


class TestRule3TimeSleepInAsync:
    def test_no_time_sleep_inside_async_defs(self):
        offenders = []
        for path in _iter_py_files(_SCAN_ROOTS):
            rel = str(path.relative_to(_REPO_ROOT))
            for lineno, hit in _find_offenders(path, "time.sleep"):
                if (rel, "time.sleep", lineno) in _WHITELIST:
                    continue
                offenders.append(f"{rel}:{lineno} {hit}")
        assert offenders == [], f"rule3 violated: {offenders}"
"""T44：`DASHSCOPE_COMPAT_BASE` 单一常量源守护。

验收判据：常量仅一处定义，其余位置改为导入。
- 值不变（接口契约）：仍为 DashScope OpenAI 兼容模式 Base URL；
- 唯一定义：全仓 `.py` 中该 URL 字面量赋值仅 `common/llm/constants.py` 一处；
- 消费方导入：provider / reliability.orchestrator / qc llm_reviewer 均从
  `common.llm.constants` 导入，不再本地定义。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from common.llm.constants import DASHSCOPE_COMPAT_BASE

ROOT = Path(__file__).resolve().parents[2]
URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

CONSUMERS = [
    ROOT / "common/llm/provider.py",
    ROOT / "common/llm/reliability/orchestrator.py",
    ROOT / "services/qc-service/app/services/llm_reviewer.py",
]


def _string_assign_targets(path: Path) -> list[str]:
    """返回该文件中「赋值为 URL 字符串字面量」的变量名列表。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if node.value.value == URL:
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        hits.append(t.id)
    return hits


def test_value_unchanged():
    """正常流程：常量值与整改前完全一致（不改行为，只去重）。"""
    assert DASHSCOPE_COMPAT_BASE == URL


def test_single_definition_repo_wide():
    """验收判据：全仓该 URL 字面量赋值仅一处（constants.py）。

    注：服务配置项默认值（如 complaint `LLM_DASHSCOPE_BASE_URL`、rag ASR
    端点）属独立配置语义、可被 env 覆盖，不在本项去重范围。
    """
    assign_files: list[Path] = []
    for py in ROOT.rglob("*.py"):
        # 生产代码范围：排除测试（测试断言字面量属合法用法）、.git/build 与唯一定义处
        if (
            ".git" in py.parts
            or "build" in py.parts
            or "tests" in py.parts
            or py.name.startswith("test_")
            or py.name == "constants.py"
        ):
            continue
        try:
            if _string_assign_targets(py):
                assign_files.append(py)
        except SyntaxError:
            continue
    assert assign_files == [], f"发现 constants.py 之外的 URL 字面量赋值：{assign_files}"


@pytest.mark.parametrize("path", CONSUMERS, ids=lambda p: p.name)
def test_consumer_imports_single_source(path):
    """其余位置改为导入：每个消费方 MUST 从 common.llm.constants 导入。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "common.llm.constants"
        for alias in node.names
    }
    assert "DASHSCOPE_COMPAT_BASE" in imported, f"{path.name} 未从单一常量源导入"
    assert _string_assign_targets(path) == [], f"{path.name} 仍本地定义该常量"


def test_provider_and_orchestrator_share_same_object():
    """运行时一致性：common 层两个消费方引用同一对象（非拷贝字面量）。"""
    pytest.importorskip("prometheus_client")
    from common.llm import provider
    from common.llm.reliability import orchestrator

    assert provider.DASHSCOPE_COMPAT_BASE is DASHSCOPE_COMPAT_BASE
    assert orchestrator.DASHSCOPE_COMPAT_BASE is DASHSCOPE_COMPAT_BASE

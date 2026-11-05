"""spawn_tracked 公共原语单测 + 全仓裸 create_task 守护扫描（T46 / 1.1）。

验收判据：
- spawn_tracked 返回的 Task 在模块级强引用集合内，done 后被 discard
  （断言按当前运行 loop 过滤，防 pytest 用例级 loop 串扰）；
- GC 压测下任务不被回收、正常完成；
- 无运行事件循环时 RuntimeError 向上传导（不做 asyncio.run 兜底）；
- 任务异常经 done 回调留痕（S1：tracked_task_failed error）且不吞，
  await 仍可取回异常；取消态不留痕；
- 全仓（服务/公共代码与脚本）丢弃 create_task 返回值的调用点零命中，
  豁免点须在白名单显式列名（初始空集，新豁免须评审）。
"""
from __future__ import annotations

import asyncio
import ast
import gc
import weakref
from pathlib import Path

import pytest

from common.utils.spawn import _tracked_tasks, pending_tracked_tasks, spawn_tracked

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_EXCLUDE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules",
    "openspec", "requirements_document", ".trae", "baseline",
    ".codeartsdoer",
}

# 裸 create_task（丢弃返回值）豁免白名单：{"相对路径:行号": "豁免理由"}。
# 初始为空集；确需豁免的调用点须在此显式列名并附理由（新豁免须评审）。
_CREATE_TASK_EXEMPTIONS: dict[str, str] = {}


def _find_discarded_create_tasks(source: str) -> list[int]:
    """AST 解析：返回 create_task 调用结果被丢弃（独立表达式语句）的行号。

    赋值（task = ...）、容器追加（tasks.append(...)）、return、await 形态
    均不视为丢弃（引用被持有或任务立即被等待，无 GC 逃逸风险）。
    """
    tree = ast.parse(source)
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Expr):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if (isinstance(func, ast.Attribute) and func.attr == "create_task") or (
            isinstance(func, ast.Name) and func.id == "create_task"
        ):
            lines.append(call.lineno)
    return lines


def _current_loop_tracked() -> list:
    """当前运行 loop 的在途 tracked 任务（测试断言用，防用例级 loop 串扰）。"""
    loop = asyncio.get_running_loop()
    return [t for t in _tracked_tasks if t.get_loop() is loop]


class TestSpawnTracked:
    async def test_task_held_while_running_and_discarded_after_done(self):
        """运行期任务在 tracked 集合内（当前 loop 过滤），done 后被 discard。"""
        release = asyncio.Event()

        async def work() -> None:
            await release.wait()

        task = spawn_tracked(work())
        try:
            assert task.get_loop() is asyncio.get_running_loop()
            assert _current_loop_tracked() == [task]
            assert pending_tracked_tasks() == 1
        finally:
            release.set()
        await task
        await asyncio.sleep(0)  # done_callback 经下一轮调度执行
        assert _current_loop_tracked() == []
        assert pending_tracked_tasks() == 0

    async def test_gc_pressure_does_not_kill_task(self):
        """GC 压测：丢弃局部引用并强制 gc.collect，任务不被回收且正常完成。"""
        done: list[str] = []

        async def work() -> None:
            for _ in range(3):
                await asyncio.sleep(0)
            done.append("ok")

        weak = weakref.ref(spawn_tracked(work()))
        gc.collect()
        assert weak() is not None, "tracked 任务在 GC 压测下被回收（强引用失效）"
        for _ in range(5):
            await asyncio.sleep(0)
        assert done == ["ok"], "tracked 任务未执行完成"
        await asyncio.sleep(0)
        assert pending_tracked_tasks() == 0

    async def test_task_exception_propagates_via_default_chain(self):
        """任务异常走默认 asyncio 异常链：await 时抛出，集合仍确定性移除。"""
        async def boom() -> None:
            raise RuntimeError("spawn-tracked-boom")

        task = spawn_tracked(boom())
        with pytest.raises(RuntimeError, match="spawn-tracked-boom"):
            await task
        await asyncio.sleep(0)
        assert _current_loop_tracked() == []

    async def test_fire_and_forget_failure_logged(self, monkeypatch):
        """S1：fire-and-forget 任务异常经 done 回调以 tracked_task_failed 留痕，
        不吞异常、不阻断；取消态不记录。"""
        from common.utils import spawn as spawn_mod

        calls: list[tuple[tuple, dict]] = []

        class _StubLogger:
            def error(self, *args, **kwargs):
                calls.append((args, kwargs))

        monkeypatch.setattr(spawn_mod, "logger", _StubLogger())

        async def boom() -> None:
            raise RuntimeError("tracked-boom-s1")

        fire_and_forget = spawn_tracked(boom())  # 不 await：纯 fire-and-forget
        with pytest.raises(RuntimeError, match="tracked-boom-s1"):
            await fire_and_forget
        await asyncio.sleep(0)  # done_callback 执行

        assert len(calls) == 1
        assert calls[0][0][0] == "tracked_task_failed"

        # 取消态：正常关停语义，不留 error
        calls.clear()

        async def hang() -> None:
            await asyncio.sleep(60)

        task = spawn_tracked(hang())
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert calls == []

    def test_no_running_loop_raises_runtime_error(self):
        """无运行事件循环：RuntimeError 向上传导，不做 asyncio.run 兜底。

        必须为同步测试：pytest-asyncio 的 async 用例自带 running loop，
        只有同步上下文才能真正验证"无 loop → RuntimeError"路径。
        """

        async def noop() -> None:
            return None

        coro = noop()
        try:
            with pytest.raises(RuntimeError):
                spawn_tracked(coro)
        finally:
            coro.close()

    async def test_multiple_spawns_tracked_independently(self):
        """多次派发独立入集合，逐个完成后逐个移除。"""
        release = asyncio.Event()

        async def work() -> None:
            await release.wait()

        t1 = spawn_tracked(work())
        t2 = spawn_tracked(work())
        # 集合无序：断言成员一致而非列表顺序
        assert set(_current_loop_tracked()) == {t1, t2}
        release.set()
        await asyncio.gather(t1, t2)
        await asyncio.sleep(0)
        assert _current_loop_tracked() == []


class TestNoDiscardedCreateTaskGuard:
    def test_scan_detects_discarded_call(self):
        """扫描器：裸表达式语句形态命中（含 loop.create_task）。"""
        src = (
            "import asyncio\n"
            "async def go():\n"
            "    asyncio.create_task(f())\n"
            "    loop.create_task(g())\n"
        )
        assert _find_discarded_create_tasks(src) == [3, 4]

    def test_scan_ignores_held_or_awaited_forms(self):
        """扫描器：赋值/容器追加/return/await 形态不命中。"""
        src = (
            "import asyncio\n"
            "async def go(loop, tasks):\n"
            "    task = asyncio.create_task(f())\n"
            "    tasks.append(asyncio.create_task(f()))\n"
            "    return asyncio.create_task(f())\n"
            "    await asyncio.create_task(f())\n"
        )
        assert _find_discarded_create_tasks(src) == []

    def test_repo_wide_zero_discarded_create_task(self):
        """全仓守护：丢弃 create_task 返回值零命中（豁免点须白名单列名）。"""
        offenders: list[str] = []
        for py in _REPO_ROOT.rglob("*.py"):
            if any(part in _SCAN_EXCLUDE_DIRS for part in py.parts):
                continue
            try:
                source = py.read_text(encoding="utf-8")
            except (OSError, SyntaxError):
                continue
            rel = py.relative_to(_REPO_ROOT).as_posix()
            for lineno in _find_discarded_create_tasks(source):
                key = f"{rel}:{lineno}"
                if key in _CREATE_TASK_EXEMPTIONS:
                    continue
                offenders.append(key)
        assert offenders == [], (
            "发现裸 create_task 丢弃返回值（GC 逃逸风险）："
            f"{offenders}；应改用 from common import spawn_tracked，"
            "或在 test_spawn_tracked.py 白名单显式列名并附理由（新豁免须评审）"
        )
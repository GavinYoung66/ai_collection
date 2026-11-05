"""后台任务派发公共原语：spawn_tracked 防 asyncio.Task GC 静默回收。

事件循环仅弱持有运行中的 Task——``asyncio.create_task(...)`` 返回值若无人
持有，任务可能在执行中被 GC（静默消失且无异常可观测）。本模块提供全仓后台
任务派发唯一入口：模块级强引用集合持有任务，完成后经 done 回调确定性移除。

参照实现：services/complaint-service/app/events/publisher.py
``_schedule_bus_publish``（第一期已验证形态，T46）。
"""
from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

import structlog

logger = structlog.get_logger()

# 后台任务强引用集合：create_task 的返回值若无人持有，事件循环仅保留弱引用，
# 任务可能在 GC 时被静默回收。add 后注册 done_callback(discard) 确定性移除。
_tracked_tasks: set[asyncio.Task[Any]] = set()


def pending_tracked_tasks() -> int:
    """在途 tracked 任务数（供单测/运维观测）。

    只统计当前运行事件循环内的任务：pytest 用例级 loop 相互独立，历史 loop
    遗留的未完成任务不应污染当前用例的计数（生产为单一常驻 loop，语义不变）。
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return len(_tracked_tasks)
    return sum(1 for task in _tracked_tasks if task.get_loop() is loop)


def _on_tracked_task_done(task: asyncio.Task[Any]) -> None:
    """done 回调：确定性移除强引用；S1 补异常留痕。

    纯 fire-and-forget 的任务异常原本只落 asyncio 默认 handler（链路无
    结构化日志）。此处经 ``task.exception()`` 取出并记录 error——取引用
    同时消解 "exception was never retrieved" 告警；取消态不记（正常关停）。
    异常仍随 Task 对象可被调用方 await 获取，原语不吞异常。
    """
    _tracked_tasks.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error(
            "tracked_task_failed",
            error=repr(exc),
            exc_info=exc,
        )


def spawn_tracked(coro: Coroutine[Any, Any, Any]) -> asyncio.Task[Any]:
    """在当前事件循环上调度后台任务，并以模块级强引用持有至其结束。

    - 必须在运行中的事件循环内调用；无 loop 时 RuntimeError 向上传导，
      由调用方自行降级（本原语不做 asyncio.run 兜底，避免隐藏调用方环境错误）。
    - 任务异常经 done 回调以 ``tracked_task_failed`` error 留痕（S1），
      不吞异常、不阻断；调用方仍可 await 获取异常。
    - 返回 Task 供调用方按需 await/取消（不强制）。
    """
    task = asyncio.get_running_loop().create_task(coro)
    _tracked_tasks.add(task)
    task.add_done_callback(_on_tracked_task_done)
    return task
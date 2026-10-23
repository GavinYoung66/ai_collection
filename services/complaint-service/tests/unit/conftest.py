"""tests/unit 目录共享 fixture。

`_drain_running_queued`：每个用例结束后，把 `app.main._running_queued` 中
残留的排队研判任务等到终结（超时取消兜底），从机制上杜绝跨用例泄漏。

背景（PR #4 master 侧 run_3 三连失败根因）：
派发任务（`_spawn_queued_complaint`）由模块级集合强引用。若用例断言前任务
未终结（慢 runner 抖动），残留任务会泄漏——下个用例读到脏集合；且该用例
事件循环关闭后，`test_lifespan_wiring` 的 lifespan shutdown 对残留 task
调 `cancel()` 会抛 `RuntimeError: Event loop is closed`。
"""
from __future__ import annotations

import asyncio

import pytest


@pytest.fixture(autouse=True)
async def _drain_running_queued():
    yield
    from app.main import _running_queued

    pending = [t for t in list(_running_queued) if not t.done()]
    if not pending:
        return
    _done, still = await asyncio.wait(pending, timeout=5)
    for t in still:
        t.cancel()
    # return_exceptions=True：取回任务异常——teardown 顺序上限流器已被模块内
    # fixture 重置，残留任务 finally 释放槽可能抛 ValueError；不取回会产生
    # "Task exception was never retrieved" 噪音。
    await asyncio.gather(*pending, return_exceptions=True)

"""T58 / 5.3：rag-service shutdown 接入 engine.dispose 单测。

验收判据：shutdown 独立函数尾部 ``await engine.dispose()`` 被调用恰好一次
（monkeypatch 计数，守护测试防回退网 11.2）。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

import pytest


class _FakeEngine:
    def __init__(self):
        self.dispose_calls = 0

    async def dispose(self):
        self.dispose_calls += 1


@pytest.mark.asyncio
async def test_shutdown_disposes_engine_once(monkeypatch):
    """rag shutdown()：engine.dispose 恰好一次（deps 全局 client 为 None 路径）。"""
    import common.db.session as db_session
    from app import main as rag_main

    fake = _FakeEngine()
    monkeypatch.setattr(db_session, "engine", fake)
    monkeypatch.setattr(rag_main, "_unwire_resilience_hooks", lambda: None)

    await rag_main.shutdown()

    assert fake.dispose_calls == 1, "shutdown 尾部应恰好 dispose 一次"
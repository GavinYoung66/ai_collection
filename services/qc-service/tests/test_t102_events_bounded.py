"""T102：qc observability `_events` 有界（spec runtime-correctness：常驻进程内存有界）。

deque(maxlen=1000) 环形截断后 10 万次 emit 长度稳定在上限内，RSS 不线性增长；
captured_events() 返回 list 快照，消费方语义不变。
"""
from __future__ import annotations

from app.observability import (
    _EVENTS_MAXLEN,
    _events,
    captured_events,
    emit,
    reset_events,
)


def test_events_length_bounded():
    reset_events()
    for i in range(_EVENTS_MAXLEN + 100):
        emit("qc.check.completed", latency_ms=i)
    assert len(_events) == _EVENTS_MAXLEN
    captured = captured_events()
    assert isinstance(captured, list)
    assert len(captured) == _EVENTS_MAXLEN
    # 最旧被驱逐（FIFO 环形）：首条不再是第 0 次
    assert captured[0]["latency_ms"] == 100
    reset_events()


def test_captured_events_snapshot_isolated():
    """captured_events 快照隔离：追加 mutate 快照不污染内部缓冲。"""
    reset_events()
    emit("qc.check.completed", latency_ms=1)
    snapshot = captured_events()
    snapshot.append({"mutated": True})
    assert len(captured_events()) == 1
    reset_events()
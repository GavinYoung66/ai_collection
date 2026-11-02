"""T08 回归：qc.check.completed 早于落库时进入延迟补偿，不静默丢弃。"""
from __future__ import annotations

import time

import pytest

from app.core.config import dp_settings
from app.events import subscriber as sub_mod
from app.events.subscriber import (
    compensate_pending_retries,
    get_compensation_stats,
    handle_qc_check_completed,
)


@pytest.fixture(autouse=True)
def _reset_retry_state(monkeypatch):
    sub_mod._pending_retries.clear()
    for key in sub_mod._RETRY_STATS:
        sub_mod._RETRY_STATS[key] = 0
    yield
    sub_mod._pending_retries.clear()
    for key in sub_mod._RETRY_STATS:
        sub_mod._RETRY_STATS[key] = 0


def _payload(**overrides):
    data = {
        "case_id": "case-t08",
        "session_id": "sess-t08",
        "qc_record_id": "qc-t08",
        "score": 88,
        "risk": "medium",
        "trace_id": "tr-t08",
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_event_before_record_enqueues_instead_of_skip(monkeypatch):
    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_false)

    out = await handle_qc_check_completed(_payload())

    assert out["status"] == "pending_retry"
    assert out["reason"] == "qc_record_not_found"
    assert out["pending"] == 1
    stats = get_compensation_stats()
    assert stats["enqueued"] == 1
    assert stats["pending"] == 1
    assert stats["exhausted"] == 0


@pytest.mark.asyncio
async def test_compensation_retries_then_writes_snapshot(monkeypatch):
    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_false)
    await handle_qc_check_completed(_payload())

    # 第一次补偿：记录仍不可见 → 计数 +1，保留待补偿
    stats = await compensate_pending_retries(now=time.time() + 10)
    assert stats["pending"] == 1
    assert sub_mod._pending_retries["sess-t08"].attempts == 1
    assert stats["succeeded"] == 0

    # 记录落库后：第二次补偿成功回写并出队
    updates: list[tuple[str, dict]] = []

    async def _fake_update(case_id, payload):
        updates.append((case_id, payload))
        return True

    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_true)
    monkeypatch.setattr(sub_mod, "_update_snapshot_qc_cols", _fake_update)

    stats = await compensate_pending_retries(now=time.time() + 20)
    assert stats["pending"] == 0
    assert stats["succeeded"] == 1
    assert stats["exhausted"] == 0
    assert updates[0][0] == "case-t08"
    assert updates[0][1]["score"] == 88


@pytest.mark.asyncio
async def test_compensation_exhausts_with_observable_count(monkeypatch):
    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_false)
    monkeypatch.setattr(dp_settings, "QC_RECOVERY_MAX_ATTEMPTS", 2)
    await handle_qc_check_completed(_payload())

    stats = await compensate_pending_retries(now=time.time() + 10)
    assert stats["pending"] == 1  # attempts=1，未耗尽

    stats = await compensate_pending_retries(now=time.time() + 20)
    assert stats["pending"] == 0
    assert stats["exhausted"] == 1


@pytest.mark.asyncio
async def test_missing_session_is_skipped_and_counted(monkeypatch):
    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_false)

    out = await handle_qc_check_completed(_payload(session_id=""))

    assert out["status"] == "skipped"
    assert get_compensation_stats()["skipped_missing_session"] == 1
    assert get_compensation_stats()["pending"] == 0


@pytest.mark.asyncio
async def test_record_visible_but_snapshot_update_fails_stays_pending(monkeypatch):
    """记录已可见但快照回写失败：不得谎报成功，仍按次数上限重试。"""
    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_false)
    await handle_qc_check_completed(_payload())

    async def _update_false(*_args, **_kwargs):
        return False

    monkeypatch.setattr(sub_mod, "_confirm_qc_record", _async_true)
    monkeypatch.setattr(sub_mod, "_update_snapshot_qc_cols", _update_false)

    stats = await compensate_pending_retries(now=time.time() + 10)
    assert stats["pending"] == 1
    assert stats["succeeded"] == 0
    assert sub_mod._pending_retries["sess-t08"].attempts == 1


@pytest.mark.asyncio
async def test_compensation_loop_cancels_cleanly():
    """后台补偿循环可被取消（lifespan 关停路径）。"""
    import asyncio

    subscriber = sub_mod.QcCheckSubscriber(settings=dp_settings)
    task = asyncio.create_task(subscriber.compensation_loop())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


async def _async_true(*_args, **_kwargs):
    return True


async def _async_false(*_args, **_kwargs):
    return False

"""工单会话服务（8.1）。

- 幂等真相：complaint_id → session_id 持久映射（先查映射，再 SETNX）
- SETNX csmp:complaint:lock:{id} TTL=1800(30min)
- session 创建，初始 pending
- 10 状态流转（TaskStatus）；transition 接受 str | Enum
- 全局任务超时 30min → pending_review
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional, Union

from app.models.state import TaskStatus
from app.utils.redis_keys import TTL_COMPLAINT_LOCK, complaint_lock_key

StatusLike = Union[TaskStatus, str]

# 合法状态流转映射（from -> set of to）
_TRANSITIONS: dict[str, set[str]] = {
    TaskStatus.pending.value: {TaskStatus.evidence_gathering.value},
    TaskStatus.evidence_gathering.value: {
        TaskStatus.reflection_retry.value,
        TaskStatus.judging.value,
    },
    TaskStatus.reflection_retry.value: {TaskStatus.judging.value},
    TaskStatus.judging.value: {TaskStatus.draft_generated.value},
    TaskStatus.draft_generated.value: {TaskStatus.pending_review.value},
    TaskStatus.pending_review.value: {
        TaskStatus.resolved.value,  # 兼容旧 approve 路径
        TaskStatus.pending_callback.value,  # V4 定责
        TaskStatus.evidence_gathering.value,
        TaskStatus.judging.value,
        TaskStatus.pending_senior_review.value,
    },
    TaskStatus.pending_senior_review.value: {
        TaskStatus.resolved.value,
        TaskStatus.pending_callback.value,
        TaskStatus.evidence_gathering.value,
    },
    TaskStatus.pending_callback.value: {
        TaskStatus.resolved.value,  # 段 B 回电后
        TaskStatus.evidence_gathering.value,  # 退回补证
    },
    TaskStatus.resolved.value: {TaskStatus.closed.value, TaskStatus.archived.value},
    TaskStatus.closed.value: {TaskStatus.archived.value},
    TaskStatus.archived.value: set(),
}

TIMEOUT_MINUTES = 30

# 多步推进仅允许「研判流水线」内状态（禁止自动跳到 resolved/closed）
_PIPELINE = {
    TaskStatus.pending.value,
    TaskStatus.evidence_gathering.value,
    TaskStatus.reflection_retry.value,
    TaskStatus.judging.value,
    TaskStatus.draft_generated.value,
    TaskStatus.pending_review.value,
    TaskStatus.pending_senior_review.value,
    TaskStatus.pending_callback.value,
}


def _normalize_status(to_status: StatusLike) -> TaskStatus:
    if isinstance(to_status, TaskStatus):
        return to_status
    return TaskStatus(str(to_status))


class _InMemoryRedis:
    """最小 Redis：set/get/setnx/expire。"""

    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, k: str) -> Optional[str]:
        return self._d.get(k)

    def set(self, k: str, v: str, ex: Optional[int] = None) -> bool:
        self._d[k] = v
        return True

    def setnx(self, k: str, v: str) -> bool:
        if k in self._d:
            return False
        self._d[k] = v
        return True

    def expire(self, k: str, ttl: int) -> bool:
        return k in self._d

    def delete(self, k: str) -> int:
        return 1 if self._d.pop(k, None) is not None else 0


class SessionService:
    def __init__(self, redis_client: Any = None, clock: Any = None) -> None:
        self._redis = redis_client or _InMemoryRedis()
        self._clock = clock or time.time
        self._store: dict[str, dict] = {}
        self._by_complaint: dict[str, str] = {}  # complaint_id → session_id 持久映射
        self._lock = threading.Lock()

    def _find_by_complaint(self, complaint_id: str) -> Optional[str]:
        sid = self._by_complaint.get(complaint_id)
        if sid and sid in self._store:
            return sid
        for sid, rec in self._store.items():
            if rec["complaint_id"] == complaint_id:
                self._by_complaint[complaint_id] = sid
                return sid
        return None

    # ── 接入幂等 ──

    def create_session(self, complaint_id: str, complaint_text: str,
                       session_id: Optional[str] = None) -> str:
        # P0：先查持久映射，不依赖锁 TTL
        existing = self._find_by_complaint(complaint_id)
        if existing is not None:
            return existing

        lock_key = complaint_lock_key(complaint_id)
        acquired = self._redis.setnx(lock_key, "1")
        if not acquired:
            # 竞态：他方持锁，再查一次映射
            existing = self._find_by_complaint(complaint_id)
            if existing is not None:
                return existing
        else:
            self._redis.expire(lock_key, TTL_COMPLAINT_LOCK)

        with self._lock:
            # 双重检查
            existing = self._find_by_complaint(complaint_id)
            if existing is not None:
                return existing
            sid = session_id or str(uuid.uuid4())
            now = datetime.now(timezone.utc)
            self._store[sid] = {
                "session_id": sid,
                "complaint_id": complaint_id,
                "complaint_text": complaint_text,
                "task_status": TaskStatus.pending,
                "created_at": now,
                "updated_at": now,
                "status_changed_at": now,
            }
            self._by_complaint[complaint_id] = sid
            return sid

    # ── 状态流转 ──

    def get_session(self, session_id: str) -> Optional[dict]:
        return self._store.get(session_id)

    def get_status(self, session_id: str) -> Optional[str]:
        rec = self._store.get(session_id)
        return rec["task_status"].value if rec else None

    def transition(self, session_id: str, to_status: StatusLike) -> bool:
        """单步合法流转；to_status 可为 TaskStatus 或 str。

        若目标非相邻态，仅当整条最短路径落在研判流水线内时才多步推进
        （如 pending → pending_review）；禁止自动跳到 resolved/closed。
        """
        target = _normalize_status(to_status)
        rec = self._store.get(session_id)
        if rec is None:
            return False
        cur = rec["task_status"].value
        if cur == target.value:
            return True
        # 单步
        if target.value in _TRANSITIONS.get(cur, set()):
            rec["task_status"] = target
            rec["status_changed_at"] = datetime.now(timezone.utc)
            rec["updated_at"] = rec["status_changed_at"]
            return True
        # 多步：仅流水线内
        if target.value not in _PIPELINE:
            return False
        path = self._shortest_path(cur, target.value)
        if not path or any(p not in _PIPELINE for p in path):
            return False
        for step in path:
            rec["task_status"] = TaskStatus(step)
            rec["status_changed_at"] = datetime.now(timezone.utc)
            rec["updated_at"] = rec["status_changed_at"]
        return True

    @staticmethod
    def _shortest_path(start: str, goal: str) -> list[str]:
        if start == goal:
            return []
        q: deque[tuple[str, list[str]]] = deque([(start, [])])
        seen = {start}
        while q:
            node, path = q.popleft()
            for nxt in _TRANSITIONS.get(node, set()):
                if nxt in seen:
                    continue
                np = path + [nxt]
                if nxt == goal:
                    return np
                seen.add(nxt)
                q.append((nxt, np))
        return []

    # ── 超时扫描 ──

    def timeout_sessions(self, timeout_minutes: int = TIMEOUT_MINUTES) -> list[str]:
        """将 judging/draft_generated 超过 timeout_minutes 的工单转 pending_review。"""
        now = self._clock()
        timeout_sec = timeout_minutes * 60
        hit: list[str] = []
        for sid, rec in self._store.items():
            if rec["task_status"] in (TaskStatus.judging, TaskStatus.draft_generated):
                elapsed = now - rec["status_changed_at"].timestamp()
                if elapsed > timeout_sec:
                    rec["task_status"] = TaskStatus.pending_review
                    rec["status_changed_at"] = datetime.now(timezone.utc)
                    hit.append(sid)
        return hit

    # ── 对账辅助 ──

    def list_stale_draft_generated(self, stale_hours: float = 1.0) -> list[str]:
        now = self._clock()
        return [
            sid for sid, rec in self._store.items()
            if rec["task_status"] == TaskStatus.draft_generated
            and now - rec["status_changed_at"].timestamp() > stale_hours * 3600
        ]


# 全局单例
session_service = SessionService()

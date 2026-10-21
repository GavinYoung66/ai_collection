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

import structlog

from app.models.state import TaskStatus
from app.utils.redis_keys import TTL_COMPLAINT_LOCK, complaint_lock_key

logger = structlog.get_logger()

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
        # PG 写穿是否已降级（fail-open：写失败只告警，不回滚内存流转）
        self._persist_degraded = False

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

    # ── PG 持久化（异步边界）────────────────────────────────
    #
    # 设计：本类的同步 API 与内存 _store 是**单进程内的读权威**，签名一律不变；
    # PG 持久化只体现在下面这组 a* 方法里，由调用方（ComplaintService 与路由）
    # 在已是 async 的位置显式调用，避免把 21 处调用点全部 async 化。
    #
    # 权威边界：内存命中时内存优先（它是活的、最新的；PG 是 write-behind，
    # 可能落后一次 flush）；进程重启/换副本/内存无此 sid 时 PG 才成为权威。

    def _persist_enabled(self) -> bool:
        """PG 写穿是否启用。与 _persist_complaint_row 同一道门，保证单测不碰 PG。"""
        try:
            from app.config import settings

            if not getattr(settings, "COMPLAINT_SESSION_PERSIST", True):
                return False
            return not getattr(settings, "USE_MOCK_MCP", True)
        except Exception:  # noqa: BLE001
            return False

    @property
    def persist_degraded(self) -> bool:
        """PG 写穿**当前**是否处于降级。

        刻意做成可自愈：任何一次成功写入都会复位。若做成只置不清的闩锁，
        一次瞬时抖动会让 /health 永久显示 degraded，运维无法据此判断现状。
        """
        return self._persist_degraded

    def _mark_persist_ok(self) -> None:
        self._persist_degraded = False

    async def aensure_persisted(self, session_id: str,
                                trace_id: Optional[str] = None) -> bool:
        """bootstrap INSERT：把内存新建的 session 落一行到 complaint_sessions。

        幂等（已存在直接返回 True）。**失败不抛**——session 行是恢复辅助，
        缺了只是"这条工单重启后不可 resume"，不应让建单 500。
        FK 依赖：complaint_id 指向 complaints.id，必须在工单行落库之后调用。
        """
        if not self._persist_enabled():
            return False
        rec = self._store.get(session_id)
        if rec is None:
            return False

        try:
            from sqlalchemy import select

            from app.models.complaint_session import ComplaintSession
            from common.db.session import async_session_factory

            async with async_session_factory() as db:
                found = await db.execute(
                    select(ComplaintSession.id)
                    .where(ComplaintSession.session_id == session_id)
                    .limit(1)
                )
                if found.scalar_one_or_none() is not None:
                    self._mark_persist_ok()
                    return True

                row = ComplaintSession(
                    session_id=session_id,
                    trace_id=trace_id or session_id,
                    complaint_id=uuid.UUID(str(rec["complaint_id"])),
                    complaint_text=rec.get("complaint_text") or "",
                    task_status=rec["task_status"].value,
                )
                db.add(row)
                await db.commit()
            self._mark_persist_ok()
            return True
        except Exception as e:  # noqa: BLE001 — fail-open
            self._persist_degraded = True
            logger.warning("session_persist_failed", op="ensure",
                           session_id=session_id, error=str(e))
            return False

    async def aflush_session(self, session_id: str) -> bool:
        """把内存中的 task_status 全量 UPDATE 到 PG。

        幂等：每次写全量状态，故上一轮 flush 失败无需重试——下一轮天然自愈。
        rowcount==0 表示该 session 未 bootstrap（mock 模式/测试），静默返回。
        update_time 由 ORM 的 onupdate=func.now() 自动刷新。
        """
        if not self._persist_enabled():
            return False
        rec = self._store.get(session_id)
        if rec is None:
            return False

        try:
            from sqlalchemy import update as sa_update

            from app.models.complaint_session import ComplaintSession
            from common.db.session import async_session_factory

            async with async_session_factory() as db:
                res = await db.execute(
                    sa_update(ComplaintSession)
                    .where(ComplaintSession.session_id == session_id)
                    .values(task_status=rec["task_status"].value)
                )
                await db.commit()
                if res.rowcount == 0:
                    logger.debug("session_flush_no_row", session_id=session_id)
                    return False
            self._mark_persist_ok()
            return True
        except Exception as e:  # noqa: BLE001 — fail-open
            self._persist_degraded = True
            logger.warning("session_persist_failed", op="flush",
                           session_id=session_id, error=str(e))
            return False

    @staticmethod
    def _row_to_record(row: Any) -> dict:
        """ORM 行 → 内存记录结构（与 create_session 写入的形状对齐）。"""
        changed = row.update_time
        return {
            "session_id": row.session_id,
            "complaint_id": str(row.complaint_id),
            "complaint_text": row.complaint_text or "",
            "task_status": _normalize_status(row.task_status),
            "created_at": row.create_time,
            "updated_at": changed,
            "status_changed_at": changed,
        }

    async def aload_session(self, session_id: str, *,
                            force: bool = False) -> Optional[dict]:
        """PG → 内存回填。默认内存优先；force=True 时才用 PG 覆盖（灾备恢复专用）。"""
        if not force and self._store.get(session_id) is not None:
            return self._store[session_id]

        try:
            from sqlalchemy import select

            from app.models.complaint_session import ComplaintSession
            from common.db.session import async_session_factory

            async with async_session_factory() as db:
                res = await db.execute(
                    select(ComplaintSession)
                    .where(ComplaintSession.session_id == session_id,
                           ComplaintSession.is_delete == "0")
                    .limit(1)
                )
                row = res.scalar_one_or_none()
            if row is None:
                return None

            rec = self._row_to_record(row)
            with self._lock:
                self._store[session_id] = rec
                self._by_complaint[str(row.complaint_id)] = session_id
            return rec
        except Exception as e:  # noqa: BLE001
            logger.warning("session_load_failed", session_id=session_id, error=str(e))
            return None

    async def aload_by_complaint(self, complaint_id: str) -> Optional[str]:
        """complaint_id → session_id。内存未命中时回源 PG。"""
        sid = self._by_complaint.get(complaint_id)
        if sid and sid in self._store:
            return sid

        try:
            from sqlalchemy import select

            from app.models.complaint_session import ComplaintSession
            from common.db.session import async_session_factory

            async with async_session_factory() as db:
                res = await db.execute(
                    select(ComplaintSession.session_id)
                    .where(
                        ComplaintSession.complaint_id == uuid.UUID(str(complaint_id)),
                        ComplaintSession.is_delete == "0",
                    )
                    .limit(1)
                )
                row = res.scalar_one_or_none()
            if row is None:
                return None
            return str(row)
        except Exception as e:  # noqa: BLE001 — 非法 UUID / 连不上都返回 None
            logger.debug("session_load_by_complaint_miss",
                         complaint_id=complaint_id, error=str(e))
            return None

    # ── Step 6：图产出快照回填（单写点）──────────────────────

    # 只回填「图产出」列。状态机列（task_status/时间戳）归 transition + aflush_session，
    # 此处绝不碰——避免两处写同一列造成双写不一致。
    _SNAPSHOT_COLUMNS = (
        "case_evidence",
        "cross_case_evidence",
        "historical_complaint_evidence",
        "reflection_report",
        "fact_timeline",
        "compliance_opinion",
        "user_demand",
        "verdict_draft",
        "judgment_package",
        "exception_info",
        "human_review",
        "loop_count",
        "total_tokens_consumed",
    )

    @staticmethod
    def _jsonable(value: Any) -> Any:
        """把图状态值转成可入 JSONB 的形式（pydantic 模型 / 枚举 / 容器）。"""
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if hasattr(value, "model_dump"):
            try:
                return value.model_dump(mode="json")
            except Exception:  # noqa: BLE001 — 非 pydantic 的 model_dump 签名
                return str(value)
        if isinstance(value, dict):
            return {str(k): SessionService._jsonable(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [SessionService._jsonable(v) for v in value]
        if hasattr(value, "value"):  # Enum
            return value.value
        return str(value)

    async def aflush_graph_snapshot(self, session_id: str, snapshot: dict) -> bool:
        """把图终态一次性写入 complaint_sessions 的 JSONB 产出列。

        **唯一写点**：图跑完时调用一次，不按 transition 增量写。
        权威边界：这些列是**只写不读**的快照，resume 一律只认 LangGraph 的
        checkpoint 三表；任何人不得从这些列反推图状态（见 ORM 类 docstring）。

        fail-open：失败只告警，不影响已完成的研判与状态流转。
        """
        if not self._persist_enabled() or not isinstance(snapshot, dict):
            return False

        cols = {
            name: self._jsonable(snapshot.get(name))
            for name in self._SNAPSHOT_COLUMNS
            if name in snapshot
        }
        if not cols:
            return False

        try:
            from sqlalchemy import update as sa_update

            from app.models.complaint_session import ComplaintSession
            from common.db.session import async_session_factory

            async with async_session_factory() as db:
                res = await db.execute(
                    sa_update(ComplaintSession)
                    .where(ComplaintSession.session_id == session_id)
                    .values(**cols)
                )
                await db.commit()
                if res.rowcount == 0:
                    logger.debug("session_snapshot_no_row", session_id=session_id)
                    return False
            self._mark_persist_ok()
            return True
        except Exception as e:  # noqa: BLE001 — fail-open
            self._persist_degraded = True
            logger.warning("session_persist_failed", op="snapshot",
                           session_id=session_id, error=str(e))
            return False

    # ── B1：PG 版超时扫描（重启后从真相源恢复）────────────────
    async def atimeout_sessions_stale(
        self, timeout_minutes: int = TIMEOUT_MINUTES
    ) -> list[str]:
        """把 PG 里超期的 judging/draft_generated 工单转 pending_review。

        这是「进程崩溃/重启后」的关键路径：同步版 `timeout_sessions` 只扫内存
        `_store`，而重启后内存为空，滞留工单永远查不到。本方法直接查 PG 真相源，
        一条 `UPDATE ... RETURNING` 原子完成，天然幂等（第二次查不到）。

        超期判据用 `update_time`（≈ 内存 `status_changed_at`，flush 后同步）：
        ORM 的 `update_time` 带 `onupdate=now()`，每次 `aflush_session` 都会刷新。
        不回写内存（重启后本无内存态）；不写 exception_info（与同步版
        `timeout_sessions` 语义对齐——对账转人工只改状态，不伪造错误标记）。

        mock 模式（session 不落 PG）下直接返回 []，由同步版覆盖单进程场景。
        """
        if not self._persist_enabled():
            return []

        from sqlalchemy import text

        sql = text(
            """
            UPDATE complaint_sessions
            SET task_status = 'pending_review'
            WHERE task_status IN ('pending', 'judging', 'draft_generated')
              AND is_delete = '0'
              AND update_time < now() - (:minutes * interval '1 minute')
            RETURNING session_id
            """
        )
        try:
            from common.db.session import async_session_factory

            async with async_session_factory() as db:
                cur = await db.execute(sql, {"minutes": timeout_minutes})
                timed_out = [str(r[0]) for r in cur.fetchall()]
                await db.commit()
            if timed_out:
                logger.info("atimeout_sessions_stale",
                            count=len(timed_out), minutes=timeout_minutes)
            self._mark_persist_ok()
            return timed_out
        except Exception as e:  # noqa: BLE001 — fail-open，下轮自愈
            self._persist_degraded = True
            logger.warning("atimeout_sessions_stale_failed", error=str(e))
            return []

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

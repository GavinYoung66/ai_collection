"""灾备恢复服务（9.3）。

- **checkpoint 恢复：未接线（DEBT）** —— 见 `recover_from_checkpoint` 的 docstring。
  真正的 checkpoint 恢复入口是 `complaint_service._resume_graph_thread`。
- Redis fallback checkpoint：csmp:session:{id}:fallback TTL 72h
- 挂起积压清理：非 archived/closed 且 update>24h → pending_review
- PG 不可用：新工单 A5010，已接入 Redis 降级
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import structlog

from app.utils.redis_keys import TTL_SESSION_FALLBACK, session_fallback_key
from common.errors.codes import ErrorCode
from common.utils.time_parse import parse_epoch_or_datetime, parse_naive_or_aware

logger = structlog.get_logger()

FALLBACK_TTL = TTL_SESSION_FALLBACK  # 72h
STALE_HOURS = 24


class _InMemoryRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v, ex=None):
        self._d[k] = v
        return True

    def keys(self, pattern="*"):
        return list(self._d.keys())


class RecoveryService:
    def __init__(self, redis_client: Any = None, clock: Any = None) -> None:
        self._redis = redis_client or _InMemoryRedis()
        # T51：默认时钟返回 aware UTC datetime；仍可注入覆盖（float epoch
        # 时钟在 cleanup_stale 内归一，兼容旧注入方）
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._pg_available = True

    def set_pg_available(self, available: bool) -> None:
        self._pg_available = available

    def recover_from_checkpoint(self, session_id: str,
                                 checkpointer: Any = None) -> tuple[bool, Optional[str]]:
        """[未接线 / DEBT] 本方法的参数形状与 checkpointer 契约不符，勿使用。

        两处错误：
        1. `checkpointer.get(session_id)` 传的是裸字符串，而 LangGraph 的
           `BaseCheckpointSaver.get(config)` 要的是 `RunnableConfig`
           （`{"configurable": {"thread_id": ...}}`）——会把 coroutine/字符串
           索引错误吞进 except，永远返回 pending_review。
        2. 即便修好形状，用 `session_id` 当 `thread_id` 也是错的：全仓
           `thread_id` 恒等于 `complaint_id`（见 complaint_service 的三个调用点）。

        真正的 checkpoint 恢复入口是
        `complaint_service._resume_graph_thread` 里的
        `graph.aget_state({"configurable": {"thread_id": complaint_id}})`。
        当前生产零调用方；修一半比不修更危险（会造成"灾备恢复已实现"的错觉）。
        """
        if not self._pg_available:
            return self.recover_from_fallback(session_id)
        try:
            if checkpointer is None:
                raise RuntimeError("no checkpointer")
            state = checkpointer.get(session_id)
            if state is None:
                return False, "pending_review"
            return True, None
        except Exception as e:  # noqa: BLE001 — 保留 fail-safe 返回值，但不再静默
            logger.warning("recovery_checkpointer_unwired",
                           session_id=session_id, error=str(e))
            return False, "pending_review"

    def save_fallback(self, session_id: str, state: dict) -> None:
        key = session_fallback_key(session_id)
        self._redis.set(key, json.dumps(state), ex=FALLBACK_TTL)

    def recover_from_fallback(self, session_id: str) -> tuple[bool, Optional[str]]:
        key = session_fallback_key(session_id)
        raw = self._redis.get(key)
        if raw is None:
            return False, "pending_review"
        try:
            state = json.loads(raw)
            state["model_degraded"] = True
            return True, None
        except Exception as exc:  # noqa: BLE001
            # T71：恢复 JSON 损坏 → fallback 不可用，降级语义值得暴露
            logger.warning(
                "recovery_fallback_json_corrupted",
                session_id=session_id,
                error=str(exc),
                exc_info=True,
            )
            return False, "pending_review"

    def cleanup_stale(self, sessions: dict[str, dict]) -> list[str]:
        """T51：时间域统一——now/updated 收敛 aware datetime 后比较。

        - naive ``updated_at`` 按业务时区（Asia/Shanghai）解释（time_parse 单源）；
        - 残留 float 旧数据走 epoch 防御分支（UTC 收敛，不崩不误删）；
        - 非法时间戳保守跳过（不崩、不误标）；
        - 比较统一 ``(now - updated).total_seconds()``。
        """
        now = self._clock()
        if isinstance(now, (int, float)):
            # 兼容注入的 float epoch 时钟（旧口径）：收敛为 aware UTC
            now = datetime.fromtimestamp(float(now), tz=timezone.utc)
        else:
            now = parse_naive_or_aware(now)
        cleaned: list[str] = []
        for sid, rec in sessions.items():
            status = rec.get("task_status")
            if status in ("archived", "closed"):
                continue
            updated = rec.get("updated_at")
            if updated is None:
                continue
            try:
                updated_dt = parse_epoch_or_datetime(updated)
            except ValueError:
                # 非法时间戳保守跳过：不崩、不误标（T51）
                continue
            if (now - updated_dt).total_seconds() > STALE_HOURS * 3600:
                rec["task_status"] = "pending_review"
                cleaned.append(sid)
        return cleaned

    def accept_new_complaint(self) -> tuple[bool, Optional[str]]:
        if not self._pg_available:
            return False, ErrorCode.A5010.value
        return True, None


recovery_service = RecoveryService()

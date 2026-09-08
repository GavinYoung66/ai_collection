"""灾备恢复服务（9.3）。

- checkpointer 恢复：成功继续，失败 pending_review
- Redis fallback checkpoint：csmp:session:{id}:fallback TTL 72h
- 挂起积压清理：非 archived/closed 且 update>24h → pending_review
- PG 不可用：新工单 A5010，已接入 Redis 降级
"""
from __future__ import annotations

import json
import time
from typing import Any, Optional

from app.utils.redis_keys import TTL_SESSION_FALLBACK, session_fallback_key
from common.errors.codes import ErrorCode

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
        self._clock = clock or time.time
        self._pg_available = True

    def set_pg_available(self, available: bool) -> None:
        self._pg_available = available

    def recover_from_checkpoint(self, session_id: str,
                                 checkpointer: Any = None) -> tuple[bool, Optional[str]]:
        if not self._pg_available:
            return self.recover_from_fallback(session_id)
        try:
            if checkpointer is None:
                raise RuntimeError("no checkpointer")
            state = checkpointer.get(session_id)
            if state is None:
                return False, "pending_review"
            return True, None
        except Exception:
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
        except Exception:
            return False, "pending_review"

    def cleanup_stale(self, sessions: dict[str, dict]) -> list[str]:
        now = self._clock()
        cleaned: list[str] = []
        for sid, rec in sessions.items():
            status = rec.get("task_status")
            if status in ("archived", "closed"):
                continue
            updated = rec.get("updated_at")
            if updated is None:
                continue
            if now - updated > STALE_HOURS * 3600:
                rec["task_status"] = "pending_review"
                cleaned.append(sid)
        return cleaned

    def accept_new_complaint(self) -> tuple[bool, Optional[str]]:
        if not self._pg_available:
            return False, ErrorCode.A5010.value
        return True, None


recovery_service = RecoveryService()

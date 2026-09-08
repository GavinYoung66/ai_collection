"""模式服务（9.1）。

- single_agent / multi_agent 双模
- gray_percent (0~100) / gray_sessions
- 一致性达标（AI-人工≥85%、互一致≥80%）后全量切换
- 切换失败回滚 A5014；双签（运营+技术负责人）
"""
from __future__ import annotations

from typing import Any, Optional

from app.events.publisher import publisher
from app.utils.redis_keys import MODE_ACTIVE_KEY, mode_gray_sessions_key
from common.errors.codes import ErrorCode

KEY_ACTIVE = MODE_ACTIVE_KEY
KEY_GRAY = "csmp:mode:gray_percent"
KEY_SESSIONS = mode_gray_sessions_key()
VALID_MODES = {"single_agent", "multi_agent"}

AI_HUMAN_THRESHOLD = 0.85
CROSS_THRESHOLD = 0.80
REQUIRED_SIGNERS = {"ops", "tech"}


class _InMemoryRedis:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, k):
        return self._d.get(k)

    def set(self, k, v, ex=None):
        self._d[k] = str(v)
        return True

    def delete(self, k):
        return self._d.pop(k, None) is not None


class ModeService:
    def __init__(self, redis_client: Any = None,
                 consistency_provider: Any = None) -> None:
        self._redis = redis_client or _InMemoryRedis()
        self._consistency = consistency_provider

    def get_active(self) -> str:
        return self._redis.get(KEY_ACTIVE) or "single_agent"

    def get_gray_percent(self) -> int:
        v = self._redis.get(KEY_GRAY)
        if v is None:
            return 0
        try:
            return int(v)
        except (TypeError, ValueError):
            return 0

    def is_gray_session(self, session_id: str) -> bool:
        sessions = self._redis.get(KEY_SESSIONS)
        if not sessions:
            return False
        return session_id in sessions.split(",")

    def should_use_single_agent(self, session_id: str) -> bool:
        if self.get_active() == "single_agent":
            return True
        percent = self.get_gray_percent()
        if percent >= 100:
            return True
        if percent <= 0:
            return False
        return (hash(session_id) % 100) < percent

    def check_consistency_ready(self) -> bool:
        if self._consistency is None:
            return False
        try:
            ai_human = self._consistency.get_ai_human_rate()
            cross = self._consistency.get_cross_rate()
            return ai_human >= AI_HUMAN_THRESHOLD and cross >= CROSS_THRESHOLD
        except Exception:
            return False

    def switch(self, mode: str, signers: list[str],
               gray_percent: Optional[int] = None) -> tuple[bool, Optional[str]]:
        """切换模式。返回 (success, error_code)。"""
        if mode not in VALID_MODES:
            return False, ErrorCode.A5014
        if not REQUIRED_SIGNERS.issubset(set(signers or [])):
            return False, ErrorCode.A5014

        # 读旧值也包进 try：Redis 异常 → A5014；脏 gray 由 get_gray_percent 兜底为 0
        old_mode = "single_agent"
        old_gray = 0
        try:
            old_mode = self.get_active()
            old_gray = self.get_gray_percent()
            self._redis.set(KEY_ACTIVE, mode)
            if gray_percent is not None:
                if not (0 <= gray_percent <= 100):
                    raise ValueError("gray_percent out of range")
                self._redis.set(KEY_GRAY, gray_percent)
            publisher.publish("complaint.mode.switched", {
                "trace_id": f"mode-{mode}",
                "from": old_mode, "to": mode,
            })
            return True, None
        except Exception:
            try:
                self._redis.set(KEY_ACTIVE, old_mode)
                self._redis.set(KEY_GRAY, old_gray)
            except Exception:
                pass
            return False, ErrorCode.A5014

    def full_switch_if_consistent(self, target_mode: str = "multi_agent") -> bool:
        if not self.check_consistency_ready():
            return False
        ok, _ = self.switch(target_mode, signers=["ops", "tech"], gray_percent=100)
        return ok


mode_service = ModeService()

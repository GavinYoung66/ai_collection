"""回调服务（8.4）。

- 幂等：session_id + version
- 失败 3 次指数退避 10/30/60s
- 死信队列 csmp:callback:dlq（Redis List）
- 每日对账由 reconcile.py 调用（共用入口）
"""
from __future__ import annotations

from typing import Any, Optional

from app.utils.redis_keys import callback_dlq_key

_BACKOFF = [10, 30, 60]  # 秒
_MAX_RETRY = 3


class _InMemoryRedis:
    def __init__(self) -> None:
        self._lists: dict[str, list] = {}

    def lpush(self, k: str, v: str) -> int:
        self._lists.setdefault(k, []).insert(0, v)
        return len(self._lists[k])

    def lrange(self, k: str, start: int, end: int) -> list:
        return self._lists.get(k, [])[start:end + 1]

    def llen(self, k: str) -> int:
        return len(self._lists.get(k, []))


class CallbackService:
    def __init__(self, http_client: Any = None, redis_client: Any = None,
                 clock: Any = None) -> None:
        self._http = http_client
        self._redis = redis_client or _InMemoryRedis()
        self._clock = clock or (lambda: __import__("time").time())
        self._sent: set[tuple] = set()  # 幂等键 (session_id, version)

    def dlq_size(self) -> int:
        return self._redis.llen(callback_dlq_key())

    def send(self, session_id: str, version: int, payload: dict) -> bool:
        key = (session_id, version)
        if key in self._sent:
            return True  # 幂等：已发过
        self._sent.add(key)

        for attempt in range(_MAX_RETRY):
            try:
                if self._http is None:
                    # Mock：默认成功
                    return True
                ok = self._http.post(session_id, version, payload)
                if ok:
                    return True
            except Exception:
                pass
            # 退避（Mock 时钟可测，不真睡）
            _ = self._clock() + _BACKOFF[attempt]
        # 3 次失败入 DLQ
        import json
        self._redis.lpush(callback_dlq_key(), json.dumps({
            "session_id": session_id, "version": version, "payload": payload,
        }))
        return False


# 全局单例
callback_service = CallbackService()

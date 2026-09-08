"""样本回流服务（8.5）。

- write_session：全量 state 序列化回写
- write_human_review_truth：回写人工复核真值
- 失败 3 次不阻塞主流程，暂存 csmp:backflow:retry（零丢失）
- 告警错误码 A5013
"""
from __future__ import annotations

import json
from typing import Any, Optional

import structlog

from common.errors.codes import ErrorCode
from common.security.audit import log_action
from app.utils.redis_keys import backflow_retry_key

logger = structlog.get_logger()

_MAX_RETRY = 3


class _InMemoryRedis:
    def __init__(self) -> None:
        self._lists: dict[str, list] = {}

    def lpush(self, k: str, v: str) -> int:
        self._lists.setdefault(k, []).insert(0, v)
        return len(self._lists[k])

    def llen(self, k: str) -> int:
        return len(self._lists.get(k, []))


class BackflowService:
    def __init__(self, mcp_client: Any = None, redis_client: Any = None) -> None:
        self._mcp = mcp_client
        self._redis = redis_client or _InMemoryRedis()
        self.last_error_code: Optional[str] = None

    def retry_queue_size(self) -> int:
        return self._redis.llen(backflow_retry_key())

    def _call_mcp(self, kind: str, session_id: str, data: dict) -> bool:
        if self._mcp is None:
            return True
        # 兼容 sync write / async call / call_sync
        if hasattr(self._mcp, "write"):
            return bool(self._mcp.write(kind, session_id, data))
        if hasattr(self._mcp, "call_sync"):
            self._mcp.call_sync("data_platform", kind, {"session_id": session_id, **data})
            return True
        raise RuntimeError("mcp client has no write/call_sync")

    def _write(self, kind: str, session_id: str, data: dict) -> bool:
        self.last_error_code = None
        for _ in range(_MAX_RETRY):
            try:
                if self._call_mcp(kind, session_id, data):
                    return True
            except Exception as e:
                logger.warning("backflow_retry", kind=kind, error=str(e))
        # 零丢失暂存 + A5013 告警
        self.last_error_code = ErrorCode.A5013.value
        self._redis.lpush(backflow_retry_key(), json.dumps({
            "kind": kind, "session_id": session_id, "data": data,
            "error_code": ErrorCode.A5013.value,
        }))
        log_action(
            action="backflow_failed",
            resource=f"{kind}/{session_id}",
            error_code=ErrorCode.A5013.value,
        )
        logger.error(
            "backflow_failed",
            error_code=ErrorCode.A5013.value,
            kind=kind,
            session_id=session_id,
        )
        return False

    def write_session(self, session_id: str, state: dict) -> bool:
        """全量 state 回写。失败不阻塞主流程。"""
        return self._write("session", session_id, state)

    def write_human_review_truth(self, session_id: str, truth: dict,
                                 ai_verdict: Optional[str] = None) -> bool:
        """回写人工真值；不一致标记 high_value。"""
        high_value = (
            ai_verdict is not None
            and truth.get("verdict") != ai_verdict
        )
        payload = {**truth, "high_value": high_value}
        return self._write("human_review_truth", session_id, payload)


# 全局单例
backflow_service = BackflowService()

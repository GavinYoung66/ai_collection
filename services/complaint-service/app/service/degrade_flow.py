"""流程降级（9.2c）—— 4 场景。

1. 反思循环超限（loop_count≥3）→ pending_review（A5004）
2. 步骤超限（max_steps）→ pending_review（A5002）
3. Token 超限 → pending_review（A5004）
4. 全局超时（30min）→ pending_review（A5010）
每个场景写 exception_info + task_status=pending_review + 发布 complaint.degraded。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from common.errors.codes import ErrorCode

LOOP_MAX = 3
QUEUE_TIMEOUT_SECONDS = 10 * 60  # 10min —— 排队 deadline
GLOBAL_TIMEOUT_SECONDS = 30 * 60  # 30min —— 执行 deadline


class DegradeFlow:
    @staticmethod
    def _exc(code: str, reason: str) -> dict:
        return {"code": code, "reason": reason,
                "ts": datetime.now(timezone.utc).isoformat()}

    def _degrade(
        self,
        code: str,
        reason: str,
        state: dict | None,
        *,
        trace_id: str | None = None,
        node: str | None = None,
    ) -> str:
        exc = self._exc(code, reason)
        if state is not None:
            state.setdefault("exception_info", []).append(exc)
            state["task_status"] = "pending_review"
        # 发布 complaint.degraded（单真相源 app.events.publisher）；
        # trace_id/node 由调用方透传真实链路上下文，缺省时才回退合成值。
        from app.events.publisher import publisher

        payload: dict[str, Any] = {
            "trace_id": trace_id or f"degrade-{code}",
            "code": code,
            "reason": reason,
        }
        if node:
            payload["node"] = node
        publisher.publish("complaint.degraded", payload)
        return "pending_review"

    def handle_loop_exceeded(self, loop_count: int, state: dict | None = None) -> str:
        if loop_count >= LOOP_MAX:
            return self._degrade(ErrorCode.A5004.value,
                                 f"反思循环超限 loop_count={loop_count}", state)
        return "continue"

    def handle_steps_exceeded(self, steps: int, max_steps: int,
                              state: dict | None = None) -> str:
        if steps >= max_steps:
            return self._degrade(ErrorCode.A5002.value,
                                 f"步骤超限 steps={steps}/{max_steps}", state)
        return "continue"

    def handle_token_exceeded(self, tokens: int, max_tokens: int,
                              state: dict | None = None) -> str:
        if tokens >= max_tokens:
            return self._degrade(ErrorCode.A5004.value,
                                 f"Token 超限 tokens={tokens}/{max_tokens}", state)
        return "continue"

    def handle_global_timeout(
        self,
        elapsed_seconds: float,
        state: dict | None = None,
        *,
        trace_id: str | None = None,
        node: str | None = None,
    ) -> str:
        if elapsed_seconds >= GLOBAL_TIMEOUT_SECONDS:
            return self._degrade(
                ErrorCode.A5010.value,
                f"全局执行超时 {elapsed_seconds:.0f}s",
                state,
                trace_id=trace_id,
                node=node,
            )
        return "continue"

    def handle_queue_timeout(
        self,
        waited_seconds: float,
        state: dict | None = None,
        *,
        trace_id: str | None = None,
        node: str | None = None,
    ) -> str:
        """排队超 10min 未获并发槽（5.1）→ A5010 转人审。"""
        if waited_seconds >= QUEUE_TIMEOUT_SECONDS:
            return self._degrade(
                ErrorCode.A5010.value,
                f"排队超时 {waited_seconds:.0f}s 未获执行槽",
                state,
                trace_id=trace_id,
                node=node,
            )
        return "continue"


# 全局单例
degrade_flow = DegradeFlow()

"""投诉服务 HTTP 客户端（data-platform → complaint-service）。

**逃生舱 / 测试专用（complaint-event-driven Decision 7 / tasks 6.3）**

- 闭环与批量验收主路径 **禁用** 本客户端：须经 EventBus 发布
  ``complaint.created``，由 complaint-service 订阅后建单 + judge；
  人审由 ``SIM_AUTO_REVIEW`` 或既有人审 API 完成，**不得**再以
  create→judge→review HTTP 三步编排闭环。
- 本模块仅保留供：单测、联调排障、显式逃生舱手工调用。
- ``run_min_loop.py`` / ``run_batch_flow.py`` 主路径不得 import 本类。

历史三步 API（逃生舱仍可用）：
1. POST /api/v1/complaints         → 创建工单（只带 complaint_text + case_id）
2. POST /api/v1/complaints/{id}/judge  → 触发研判（run_graph=true，停在人审）
3. PUT  /api/v1/complaints/{id}/review → 人审通过（review_action=approve，恢复图）

约束：
- 请求体只含 complaint_text + case_id，不传通话 session_id（3.3）
- 重复 POST 同一 case_id 不产生第二张工单，靠现有行为（3.4）
- 沿用现有超时（5 秒）与重试（3 次），失败有可查记录
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import dp_settings

logger = logging.getLogger(__name__)


def _unwrap(data: Any) -> dict[str, Any]:
    """解包统一响应 {code, message, data, trace_id}，返回 data 部分。"""
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
        return data
    return {}


class ComplaintClient:
    """投诉服务 HTTP 客户端（测试/排障逃生舱；闭环主路径禁用）。

    本机直连，不经过 api-gateway。
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        retry: int | None = None,
    ) -> None:
        self._base = (base_url or dp_settings.COMPLAINT_SERVICE_URL).rstrip("/")
        self._timeout = float(timeout if timeout is not None else dp_settings.COMPLAINT_HTTP_TIMEOUT)
        self._retry = int(retry if retry is not None else dp_settings.COMPLAINT_HTTP_RETRY)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """带重试的 HTTP 请求。失败留下可查记录。"""
        url = f"{self._base}{path}"
        last_exc: Exception | None = None
        for attempt in range(1, self._retry + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.request(method, url, json=json_body)
                    resp.raise_for_status()
                    return _unwrap(resp.json())
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "complaint_http_retry",
                    extra={
                        "method": method,
                        "url": url,
                        "attempt": attempt,
                        "max": self._retry,
                        "error": str(exc),
                    },
                )
        logger.error(
            "complaint_http_failed",
            extra={
                "method": method,
                "url": url,
                "attempts": self._retry,
                "error": str(last_exc),
            },
        )
        return {}

    async def create_complaint(
        self,
        *,
        case_id: str,
        complaint_text: str,
    ) -> str | None:
        """POST /api/v1/complaints — 只带 complaint_text + case_id，不传 session_id。

        返回 complaint_id（本闭环等于 case_id），失败返回 None。
        """
        # 3.3: 不传 session_id（该字段在投诉里是研判会话号，非通话号）
        body = {"complaint_text": complaint_text, "case_id": case_id}
        result = await self._request("POST", "/complaints", json_body=body)
        cid = result.get("complaint_id")
        if cid:
            logger.info(
                "complaint_created",
                extra={"case_id": case_id, "complaint_id": cid},
            )
        return cid

    async def judge_complaint(self, *, complaint_id: str) -> dict[str, Any]:
        """POST /api/v1/complaints/{id}/judge — body {"run_graph": true}。

        图停在 human_review，尚未 write_session。
        """
        result = await self._request(
            "POST",
            f"/complaints/{complaint_id}/judge",
            json_body={"run_graph": True},
        )
        logger.info(
            "complaint_judged",
            extra={
                "complaint_id": complaint_id,
                "task_status": result.get("task_status"),
            },
        )
        return result

    async def review_approve(self, *, complaint_id: str) -> dict[str, Any]:
        """PUT /api/v1/complaints/{id}/review — review_action=approve。

        恢复同一线程 Command(resume="approve")，进入 sample_backflow。
        """
        result = await self._request(
            "PUT",
            f"/complaints/{complaint_id}/review",
            json_body={"review_action": "approve"},
        )
        logger.info(
            "complaint_reviewed",
            extra={
                "complaint_id": complaint_id,
                "task_status": result.get("task_status"),
            },
        )
        return result

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

import asyncio
import logging
import weakref
from typing import Any

import httpx

from app.core.config import dp_settings
from common.security.path_params import ensure_safe_path_param

logger = logging.getLogger(__name__)

# T133：模块级共享出站 client（按事件循环隔离，对齐 common/llm/transport.py
# T16 形态）——同一 loop 内所有 ComplaintClient 实例（含重试循环内部）复用
# 同一 AsyncClient，保留 keep-alive 连接池，杜绝每请求×每重试新建客户端；
# loop 切换（测试/重载）时新建，防跨 loop 复用已绑定连接池。
_shared_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, httpx.AsyncClient
] = weakref.WeakKeyDictionary()


def get_shared_complaint_http_client() -> httpx.AsyncClient:
    """T133：取当前事件循环的共享 complaint 出站 client；无或已关闭则重建。"""
    loop = asyncio.get_running_loop()
    client = _shared_clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient()
        _shared_clients[loop] = client
    return client


async def aclose_shared_complaint_http_client() -> None:
    """T133：服务关停显式关闭共享 complaint 出站 client（幂等，不阻断关停）。

    对齐 common/llm/transport.aclose_shared_client 的 T57 口径：除当前
    loop 外，对旧 loop 残留 client 做防御性 best-effort 关闭，失败仅记
    warning，不影响其余关停步骤。
    """
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        return
    client = _shared_clients.pop(current, None)
    if client is not None and not client.is_closed:
        try:
            await client.aclose()
        except Exception as exc:  # noqa: BLE001 — best-effort，不阻断关停
            logger.warning(
                "complaint_shared_client_close_failed", extra={"error": str(exc)}
            )
    for loop in [lp for lp in list(_shared_clients.keys()) if lp is not current]:
        leftover = _shared_clients.pop(loop, None)
        if leftover is None or leftover.is_closed:
            continue
        try:
            await leftover.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "complaint_shared_client_stale_close_failed",
                extra={"loop_id": id(loop), "error": str(exc)},
            )


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
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = (base_url or dp_settings.COMPLAINT_SERVICE_URL).rstrip("/")
        self._timeout = float(timeout if timeout is not None else dp_settings.COMPLAINT_HTTP_TIMEOUT)
        self._retry = int(retry if retry is not None else dp_settings.COMPLAINT_HTTP_RETRY)
        # T133：显式注入的 client 优先（测试/特殊形态，生命周期由注入方管理，
        # 本类不负责关闭）；None 时走模块级共享单例——底层 AsyncClient 全程
        # 复用，关停由 lifespan 调 aclose_shared_complaint_http_client。
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        """T133：取本次请求使用的 client（注入优先，否则模块级共享单例）。"""
        if self._client is not None:
            return self._client
        return get_shared_complaint_http_client()

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
        # T133：client 提升到重试循环外——所有 attempt 复用同一连接池 client
        # （旧实现每 attempt `async with` 新建，重试放大连接池开销）。
        client = self._http()
        for attempt in range(1, self._retry + 1):
            try:
                # T133：per-request timeout 显式覆盖，保持既有 client 级超时语义
                resp = await client.request(method, url, json=json_body, timeout=self._timeout)
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
        # T111：path 参数白名单（common 单源）——complaint_id 直接拼 URL path，
        # 非法值（路径穿越/改写请求语义）显式拒绝（逃生舱语义：宁可不发请求）
        ensure_safe_path_param(complaint_id, name="complaint_id")
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
        # T111：path 参数白名单（common 单源，与 judge_complaint 同口径）
        ensure_safe_path_param(complaint_id, name="complaint_id")
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

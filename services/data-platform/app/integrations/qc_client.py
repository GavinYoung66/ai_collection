"""质检服务 HTTP 客户端（data-platform → qc-service）。

用于信封瘦身后反拉质检详情：``GET /api/v1/qc/records/{session_id}``。
禁止在事件 ``data`` 中携带 score/risk 大字段；详情一律 HTTP 拉取。
"""
from __future__ import annotations

import asyncio
import logging
import weakref
from typing import Any

import httpx

from app.core.config import dp_settings
from common.security.path_params import is_safe_path_param

logger = logging.getLogger(__name__)

# T133：模块级共享出站 client（按事件循环隔离，对齐 common/llm/transport.py
# T16 形态）——同一 loop 内所有 QcClient 实例（含重试循环内部）复用同一
# AsyncClient，保留 keep-alive 连接池，杜绝每请求×每重试新建客户端；
# loop 切换（测试/重载）时新建，防跨 loop 复用已绑定连接池。
_shared_clients: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, httpx.AsyncClient
] = weakref.WeakKeyDictionary()


def get_shared_qc_http_client() -> httpx.AsyncClient:
    """T133：取当前事件循环的共享 qc 出站 client；无或已关闭则重建。"""
    loop = asyncio.get_running_loop()
    client = _shared_clients.get(loop)
    if client is None or client.is_closed:
        client = httpx.AsyncClient()
        _shared_clients[loop] = client
    return client


async def aclose_shared_qc_http_client() -> None:
    """T133：服务关停显式关闭共享 qc 出站 client（幂等，不阻断关停）。

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
            logger.warning("qc_shared_client_close_failed", extra={"error": str(exc)})
    for loop in [lp for lp in list(_shared_clients.keys()) if lp is not current]:
        leftover = _shared_clients.pop(loop, None)
        if leftover is None or leftover.is_closed:
            continue
        try:
            await leftover.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "qc_shared_client_stale_close_failed",
                extra={"loop_id": id(loop), "error": str(exc)},
            )


def _unwrap(data: Any) -> dict[str, Any]:
    if isinstance(data, dict):
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner
        return data
    return {}


class QcClient:
    """qc-service HTTP 客户端。本机闭环调用，不经过 api-gateway。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        retry: int | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = (base_url or dp_settings.QC_SERVICE_URL).rstrip("/")
        self._timeout = float(
            timeout if timeout is not None else dp_settings.QC_HTTP_TIMEOUT
        )
        self._retry = int(retry if retry is not None else dp_settings.QC_HTTP_RETRY)
        # T133：显式注入的 client 优先（测试/特殊形态，生命周期由注入方管理，
        # 本类不负责关闭）；None 时走模块级共享单例——生产 subscriber 每事件
        # 构造 QcClient 轻量包装，底层 AsyncClient 全程复用（100 次调用仅
        # 构造 1 个 client），关停由 lifespan 调 aclose_shared_qc_http_client。
        self._client = client

    def _http(self) -> httpx.AsyncClient:
        """T133：取本次请求使用的 client（注入优先，否则模块级共享单例）。"""
        if self._client is not None:
            return self._client
        return get_shared_qc_http_client()

    async def get_record(self, session_id: str) -> dict[str, Any] | None:
        """GET /records/{session_id}；失败返回 None 并记日志。"""
        if not session_id:
            return None
        # T111：path 参数白名单（common 单源，对齐 qc→dp 的 T79 口径）——
        # payload 的 session_id 直接拼 URL path，非法值（x/../y、含 ?/#）会
        # 改写下游请求语义；非法按既有失败语义记日志返回 None，不发起请求。
        if not is_safe_path_param(session_id):
            logger.warning(
                "qc_record_invalid_session_id",
                extra={"session_id": session_id},
            )
            return None
        # Router prefix is /api/v1/qc → full path /api/v1/qc/records/{session_id}
        base = self._base.rstrip("/")
        if base.endswith("/qc"):
            url = f"{base}/records/{session_id}"
        else:
            url = f"{base}/qc/records/{session_id}"
        last_exc: Exception | None = None
        # T133：client 提升到重试循环外——所有 attempt 复用同一连接池 client
        # （旧实现每 attempt `async with` 新建，重试放大连接池开销）。
        client = self._http()
        for attempt in range(1, self._retry + 1):
            try:
                # T133：per-request timeout 显式覆盖，保持既有 client 级超时语义
                resp = await client.get(url, timeout=self._timeout)
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                body = resp.json()
                # 业务错误码（如 A2005）
                if isinstance(body, dict) and str(body.get("code", "0")) not in (
                    "0",
                    "0000",
                    "",
                ):
                    # T133：extra 的 message 是 LogRecord 保留属性（makeRecord
                    # 抛 KeyError 被重试循环吞掉）——既有潜伏缺陷，测试首次
                    # 覆盖业务码路径时暴露；改名 resp_message 修复。
                    logger.warning(
                        "qc_record_http_business_error",
                        extra={
                            "session_id": session_id,
                            "code": body.get("code"),
                            "resp_message": body.get("message"),
                        },
                    )
                    return None
                return _unwrap(body)
            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "qc_record_http_retry",
                    extra={
                        "url": url,
                        "attempt": attempt,
                        "max": self._retry,
                        "error": str(exc),
                    },
                )
        logger.error(
            "qc_record_http_failed",
            extra={
                "url": url,
                "attempts": self._retry,
                "error": str(last_exc),
            },
        )
        return None

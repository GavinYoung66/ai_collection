"""质检服务 HTTP 客户端（data-platform → qc-service）。

用于信封瘦身后反拉质检详情：``GET /api/v1/qc/records/{session_id}``。
禁止在事件 ``data`` 中携带 score/risk 大字段；详情一律 HTTP 拉取。
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import dp_settings

logger = logging.getLogger(__name__)


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
    ) -> None:
        self._base = (base_url or dp_settings.QC_SERVICE_URL).rstrip("/")
        self._timeout = float(
            timeout if timeout is not None else dp_settings.QC_HTTP_TIMEOUT
        )
        self._retry = int(retry if retry is not None else dp_settings.QC_HTTP_RETRY)

    async def get_record(self, session_id: str) -> dict[str, Any] | None:
        """GET /records/{session_id}；失败返回 None 并记日志。"""
        if not session_id:
            return None
        # Router prefix is /api/v1/qc → full path /api/v1/qc/records/{session_id}
        base = self._base.rstrip("/")
        if base.endswith("/qc"):
            url = f"{base}/records/{session_id}"
        else:
            url = f"{base}/qc/records/{session_id}"
        last_exc: Exception | None = None
        for attempt in range(1, self._retry + 1):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url)
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
                        logger.warning(
                            "qc_record_http_business_error",
                            extra={
                                "session_id": session_id,
                                "code": body.get("code"),
                                "message": body.get("message"),
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

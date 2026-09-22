"""data-platform 只读客户端：qc-source / pending-qc。禁止被 DP 回调。"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.core.config import qc_settings
from app.integrations.qc_session import QCSession, map_qc_source, unwrap_envelope
from common.llm.reliability import call_with_retry
from common.resilience.breaker import BreakerState, CircuitBreaker, make_mcp_breaker_key
from common.trace.middleware import get_trace_id

DP_RETRY_ATTEMPTS = 3
PENDING_BREAKER_FAILURES = 3
_PENDING_BREAKER_KEY = make_mcp_breaker_key("qc", "data_platform")
_SHANGHAI = ZoneInfo("Asia/Shanghai")
logger = logging.getLogger(__name__)


class DpClient:
    """GET qc-source / pending-qc。熔断 pending-qc 时跳过本轮补检。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        retry_delay: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = (base_url or qc_settings.QC_DP_BASE).rstrip("/")
        self._timeout = float(timeout if timeout is not None else qc_settings.QC_DP_TIMEOUT)
        self._retry_delay = retry_delay
        self._session_path = qc_settings.QC_DP_SESSION_PATH
        self._pending_path = qc_settings.QC_DP_PENDING_QC_PATH
        self._client = client
        self._owns_client = client is None
        self._pending_breaker = CircuitBreaker(
            _PENDING_BREAKER_KEY,
            fail_max=PENDING_BREAKER_FAILURES,
            reset_timeout=60,
        )
        self._request_log: list[tuple[str, str]] = []

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    def qc_source_url(self, session_id: str) -> str:
        path = self._session_path.format(session_id=session_id)
        if not path.startswith("/"):
            path = "/" + path
        return f"{self._base}{path}"

    def pending_qc_url(self) -> str:
        path = self._pending_path if self._pending_path.startswith("/") else f"/{self._pending_path}"
        return f"{self._base}{path}"

    @property
    def pending_circuit_open(self) -> bool:
        return self._pending_breaker.state == BreakerState.OPEN.value

    async def _get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        client = await self._http()
        headers = {"Accept": "application/json"}
        trace_id = get_trace_id()
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        self._request_log.append(("GET", url))
        resp = await client.get(url, headers=headers, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _get_json_retry(self, url: str, params: dict[str, Any] | None = None) -> Any:
        async def _once() -> Any:
            return await self._get_json(url, params)

        return await call_with_retry(
            _once,
            max_attempts=DP_RETRY_ATTEMPTS,
            delay=self._retry_delay,
            backoff=2.0,
            exceptions=(httpx.HTTPError, OSError),
        )

    async def get_qc_source(self, session_id: str) -> QCSession:
        payload = await self._get_json_retry(self.qc_source_url(session_id))
        return map_qc_source(payload)

    def parse_pending_qc(self, payload: Any) -> list[str]:
        data = unwrap_envelope(payload)
        ids = data.get("session_ids") or []
        if not isinstance(ids, list):
            return []
        return [str(item) for item in ids if item is not None and str(item) != ""]

    async def list_pending_session_ids(self, *, etl_date: str | None = None, limit: int = 100) -> list[str]:
        if not await self._pending_breaker.before_call():
            return []
        day = etl_date or datetime.now(_SHANGHAI).date().isoformat()
        capped = min(max(int(limit), 1), 500)
        url = self.pending_qc_url()
        try:
            payload = await self._get_json_retry(url, params={"etl_date": day, "limit": capped})
        except (httpx.HTTPError, OSError) as exc:
            await self._pending_breaker.record_failure(exc)
            return []
        await self._pending_breaker.record_success()
        return self.parse_pending_qc(payload)

    async def complement_pending_qc(self, *, etl_date: str | None = None, limit: int = 100) -> list[QCSession]:
        """pending-qc 熔断则跳过本轮补检，不半套拉 qc-source。"""
        if self.pending_circuit_open:
            return []
        ids = await self.list_pending_session_ids(etl_date=etl_date, limit=limit)
        if self.pending_circuit_open or not ids:
            return []
        sessions: list[QCSession] = []
        for session_id in ids:
            try:
                sessions.append(await self.get_qc_source(session_id))
            except Exception:
                logger.exception(
                    "pending-qc get_qc_source failed session_id=%s; skip, not complemented",
                    session_id,
                )
                continue
        return sessions

    def recorded_urls(self) -> list[str]:
        return [url for _method, url in self._request_log]

    def recorded_requests(self) -> list[tuple[str, str]]:
        return list(self._request_log)

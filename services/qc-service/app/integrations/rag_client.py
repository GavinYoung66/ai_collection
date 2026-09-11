"""rag 词库客户端：只 GET current.dict。禁止知识库检索 HTTP。"""
from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.core.config import qc_settings
from common.llm.reliability import call_with_retry
from common.trace.middleware import get_trace_id

DICT_PATH = "/api/v1/sensitive-words/current.dict"
RAG_RETRY_ATTEMPTS = 3
RAG_BREAKER_FAILURES = 2


def parse_current_dict(body: str) -> list[str]:
    words: list[str] = []
    for line in body.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        words.append(item)
    return words


class RagClient:
    """GET text/plain 词库。连续 2 次失败熔断 OPEN：不再 GET，直接返回上一版词表/AC。"""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        timeout: float | None = None,
        retry_delay: float = 1.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base = (base_url or qc_settings.QC_RAG_SENSITIVE_BASE).rstrip("/")
        self._timeout = float(timeout if timeout is not None else qc_settings.QC_RAG_INITIAL_TIMEOUT)
        self._retry_delay = retry_delay
        self._client = client
        self._owns_client = client is None
        self._words: list[str] = []
        self._ac_ref: tuple[str, ...] | None = None
        self._consecutive_failures = 0
        self._request_log: list[tuple[str, str]] = []

    @property
    def words(self) -> list[str]:
        return list(self._words)

    @property
    def ac_ref(self) -> tuple[str, ...] | None:
        """本轮不实现 ACFilter；词表元组作为「旧自动机」引用。"""
        return self._ac_ref

    @property
    def breaker_open(self) -> bool:
        return self._consecutive_failures >= RAG_BREAKER_FAILURES

    @property
    def fetch_healthy(self) -> bool:
        """最近一次 current.dict 拉取成功且未熔断。失败/OPEN 时 WordLoader 不得改 version。"""
        return self._consecutive_failures == 0

    def dict_url(self) -> str:
        return f"{self._base}{DICT_PATH}"

    async def aclose(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def _get_dict_once(self) -> str:
        client = await self._http()
        headers = {"Accept": "text/plain"}
        trace_id = get_trace_id()
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        url = self.dict_url()
        self._request_log.append(("GET", url))
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text

    async def fetch_current_dict(self) -> list[str]:
        """拉取词库。OPEN 时 MUST NOT GET；成功则清零计数并闭合。"""
        if self.breaker_open:
            return list(self._words)
        try:
            body = await call_with_retry(
                self._get_dict_once,
                max_attempts=RAG_RETRY_ATTEMPTS,
                delay=self._retry_delay,
                backoff=2.0,
                exceptions=(httpx.HTTPError, OSError),
            )
        except (httpx.HTTPError, OSError):
            self._consecutive_failures += 1
            return list(self._words)
        words = parse_current_dict(body)
        self._words = words
        self._ac_ref = tuple(words)
        self._consecutive_failures = 0
        return list(self._words)

    async def probe_dict_reachable(self, timeout: float = 1.0) -> bool:
        """Health 短超时单次 GET。失败 False；熔断 OPEN 且无旧词表 False。无 search。"""
        if self.breaker_open:
            return bool(self._words or self._ac_ref)
        headers = {"Accept": "text/plain"}
        trace_id = get_trace_id()
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        url = self.dict_url()
        self._request_log.append(("GET", url))
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.get(url, headers=headers)
                resp.raise_for_status()
            words = parse_current_dict(resp.text)
            if words:
                self._words = words
                self._ac_ref = tuple(words)
            return True
        except Exception:
            return False

    def recorded_urls(self) -> Sequence[str]:
        return [url for _method, url in self._request_log]

    def recorded_requests(self) -> Sequence[tuple[str, str]]:
        return list(self._request_log)

"""rag 客户端：GET current.dict；QC_R14_RAG_ENABLED 时 POST /kb/search。

search 失败 MUST NOT 累加词库熔断计数。
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import httpx

from app.core.config import qc_settings
from common.llm.reliability import call_with_retry
from common.resilience.breaker import BreakerRegistry, make_mcp_breaker_key
from common.trace.middleware import get_trace_id

DICT_PATH = "/api/v1/sensitive-words/current.dict"
SEARCH_PATH = "/api/v1/kb/search"
RAG_RETRY_ATTEMPTS = 3
RAG_BREAKER_FAILURES = 2
SEARCH_SCORE_THRESHOLD = 0.6
SEARCH_SNIPPET_MAX = 80
SEARCH_DEFAULT_CATEGORY = "script"
SEARCH_DEFAULT_TOP_K = 3
RAG_MCP_BREAKER_KEY = make_mcp_breaker_key("qc", "rag_kb")
_search_breakers = BreakerRegistry(fail_max=3, reset_timeout=60)


def rag_search_breaker():
    return _search_breakers.get_or_create(RAG_MCP_BREAKER_KEY)


def reset_rag_search_breaker() -> None:
    _search_breakers.clear()


def parse_current_dict(body: str) -> list[str]:
    words: list[str] = []
    for line in body.splitlines():
        item = line.strip()
        if not item or item.startswith("#"):
            continue
        words.append(item)
    return words


def parse_search_results(payload: Any) -> list[dict[str, Any]]:
    """统一信封 data.results[]，兼容裸 {results}。"""
    if not isinstance(payload, dict):
        return []
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(data, dict):
        return []
    results = data.get("results") or []
    return [item for item in results if isinstance(item, dict)]


def pick_search_snippet(results: list[dict[str, Any]]) -> str | None:
    if not results:
        return None
    first = results[0]
    raw_score = first.get("score")
    if raw_score is None:
        raw_score = first.get("rerank_score")
    try:
        score = float(raw_score) if raw_score is not None else 0.0
    except (TypeError, ValueError):
        score = 0.0
    if score < SEARCH_SCORE_THRESHOLD:
        return None
    content = str(first.get("content") or first.get("content_snippet") or "").strip()
    if not content:
        return None
    return content[:SEARCH_SNIPPET_MAX]


class RagClient:
    """GET text/plain 词库；可选 POST /kb/search。词库连续 2 次失败熔断 OPEN。"""

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
        self._last_search_payload: dict[str, Any] | None = None

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

    def search_url(self) -> str:
        return f"{self._base}{SEARCH_PATH}"

    @property
    def last_search_payload(self) -> dict[str, Any] | None:
        return self._last_search_payload

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
        """Health 短超时单次 GET current.dict。失败 False；不发 /kb/search。"""
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

    async def search(
        self,
        query: str,
        *,
        category: str = SEARCH_DEFAULT_CATEGORY,
        top_k: int = SEARCH_DEFAULT_TOP_K,
        answer: bool = False,
    ) -> str | None:
        """POST /api/v1/kb/search。score≥0.6 则截断 content 至 80 字；失败返回 None。

        瞬时 ``call_with_retry``（与 current.dict 同次数）；不累加 `_consecutive_failures`。
        """
        q = (query or "").strip()
        if not q:
            return None
        payload = {
            "query": q[:500],
            "category": category,
            "top_k": int(top_k),
            "answer": bool(answer),
        }
        self._last_search_payload = payload
        breaker = rag_search_breaker()
        if not await breaker.before_call():
            return None
        headers = {"Accept": "application/json", "Content-Type": "application/json"}
        trace_id = get_trace_id()
        if trace_id:
            headers["X-Trace-Id"] = trace_id
        url = self.search_url()

        async def _post_once() -> Any:
            self._request_log.append(("POST", url))
            client = await self._http()
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()

        try:
            body = await call_with_retry(
                _post_once,
                max_attempts=RAG_RETRY_ATTEMPTS,
                delay=self._retry_delay,
                backoff=2.0,
                exceptions=(httpx.HTTPError, OSError),
            )
        except (httpx.HTTPError, OSError, ValueError) as exc:
            await breaker.record_failure(exc)
            return None
        if not isinstance(body, dict):
            await breaker.record_success()
            return None
        await breaker.record_success()
        return pick_search_snippet(parse_search_results(body))

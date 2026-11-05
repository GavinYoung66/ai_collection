"""网关共享 HTTP 基座（平移自 services/complaint-service/app/mcp/bridge_http.py）。

- 统一信封解包：下游全部返回 ``{code,message,data,trace_id}``，成功取 ``data``
- 404 → ``BridgeNotFound``（查无数据语义，调用方据此返空列表/走 fallback）
- 超时默认 5s（``MCP_GATEWAY_BRIDGE_HTTP_TIMEOUT``）；传输/HTTP 错映射为 MCP 异常
- 支持注入 httpx.AsyncClient，便于单测用 MockTransport
- T22：出站前校验协议/主机白名单，拒绝回环、内网、链路本地与云元数据地址并告警

平移差异：超时默认值改读网关自身 config（源读 complaint-service settings.BRIDGE_HTTP_TIMEOUT）。
"""
from __future__ import annotations

from typing import Any, Optional

import httpx
import structlog

from app.config import settings
from app.mcp_errors import MCPError, MCPTimeoutError
from common.security.ssrf import check_outbound_url

logger = structlog.get_logger()

# T22：协议硬白名单（配置值只在其子集中生效，空配置 = 全拒）
_ALLOWED_SCHEMES = frozenset({"http", "https"})
# T22：主机默认白名单（与 config.BRIDGE_ALLOWED_HOSTS 默认值保持一致）
_DEFAULT_ALLOWED_HOSTS = ("rag-service", "data-platform", "qc-service")


def _split_csv(raw: Any) -> list[str]:
    """解析逗号分隔配置（兼容 list/tuple/set 注入）。"""
    if isinstance(raw, (list, tuple, set)):
        return [str(v).strip() for v in raw if str(v).strip()]
    return [v.strip() for v in str(raw or "").split(",") if v.strip()]


def allowed_schemes() -> set[str]:
    """出站协议白名单（小写）；配置为空或全部非法 → 空集合（全拒）。"""
    configured = {v.lower() for v in _split_csv(
        getattr(settings, "BRIDGE_ALLOWED_SCHEMES", "http,https")
    )}
    return configured & _ALLOWED_SCHEMES


def allowed_hosts() -> set[str]:
    """出站主机白名单（小写）；配置为空 → 空集合（全拒）。"""
    raw = getattr(settings, "BRIDGE_ALLOWED_HOSTS", ",".join(_DEFAULT_ALLOWED_HOSTS))
    return {v.lower() for v in _split_csv(raw)}



def validate_outbound_url(url: str, path: str) -> None:
    """出站前校验目标 URL（T22 SSRF 防护；T85 校验逻辑上移 common 单源）。

    - 协议 MUST 命中 ``BRIDGE_ALLOWED_SCHEMES``（默认 http/https）；
    - 主机 MUST 命中 ``BRIDGE_ALLOWED_HOSTS``（默认 docker network 服务名）；
    - IP 字面量形式的回环/内网/链路本地/保留/云元数据地址一律拒绝，
      即使被显式加入主机白名单；
    - 拒绝时记录 ``bridge_url_rejected`` 告警并不发起连接（转发层职责）。
    """
    reason = check_outbound_url(
        url,
        allowed_hosts=allowed_hosts(),
        allowed_schemes=allowed_schemes(),
    )
    if reason is not None:
        _reject(url, path, reason)


def _reject(url: str, path: str, reason: str) -> None:
    """记录告警并抛出 MCPError（出站前拒绝，不发起连接）。"""
    logger.warning("bridge_url_rejected", url=url, path=path, reason=reason)
    raise MCPError("_bridge", path, f"bridge url rejected: {reason}")


class BridgeNotFound(Exception):
    """下游返回 404 / 业务 not-found 码：表示查无数据，不是服务故障。"""


class BridgeHttpClient:
    """桥 HTTP 客户端：超时/信封/错误映射统一收口。"""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: Optional[float] = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._timeout = (
            float(timeout)
            if timeout is not None
            else float(getattr(settings, "BRIDGE_HTTP_TIMEOUT", 5.0))
        )
        self._client = client  # 测试注入的 client（优先）
        self._owned: httpx.AsyncClient | None = None  # 惰性持久 client（连接池复用）

    @property
    def base_url(self) -> str:
        return self._base_url

    async def _ensure_client(self) -> httpx.AsyncClient:
        """返回可复用 client：注入的优先，否则惰性创建进程内持久 client。

        持久 client 跨请求复用 TCP 连接池（keep-alive），避免每调用一次握手；
        网关进程单 event loop，惰性创建在首个请求的 loop 内安全。
        """
        if self._client is not None:
            return self._client
        if self._owned is None:
            self._owned = httpx.AsyncClient(timeout=self._timeout)
        return self._owned

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict | None = None,
        trace_id: str = "",
    ) -> Any:
        """发起内部 HTTP 调用并解包统一信封。

        Returns: 信封 ``data`` 字段。
        Raises: BridgeNotFound(404)、MCPTimeoutError、MCPError。
        """
        if not self._base_url:
            raise MCPError("_bridge", path, "bridge base url not configured")
        url = f"{self._base_url}{path}"
        # T22：SSRF 防护——出站前校验协议/主机/网段，拒绝即告警，不发起连接
        validate_outbound_url(url, path)
        headers = {"X-Trace-Id": trace_id} if trace_id else {}
        try:
            client = await self._ensure_client()
            resp = await client.request(
                method, url, json=json_body, headers=headers, timeout=self._timeout
            )
        except httpx.TimeoutException as e:
            raise MCPTimeoutError("_bridge", path, f"bridge timeout: {e}") from e
        except httpx.HTTPError as e:
            raise MCPError("_bridge", path, f"bridge http error: {e}") from e

        if resp.status_code == 404:
            raise BridgeNotFound(path)
        if resp.status_code >= 400:
            raise MCPError(
                "_bridge",
                path,
                f"bridge http {resp.status_code}: {resp.text[:200]}",
            )

        try:
            envelope = resp.json()
        except ValueError as e:
            raise MCPError("_bridge", path, "bridge response is not JSON") from e

        return _unwrap_envelope(envelope, path)

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._owned is not None:
            await self._owned.aclose()
            self._owned = None


def _unwrap_envelope(envelope: Any, path: str) -> Any:
    """统一信封解包；兼容裸 data（容错，不臆造业务字段）。"""
    if isinstance(envelope, dict) and "code" in envelope:
        code = str(envelope.get("code") or "")
        if code in ("0", "200"):
            return envelope.get("data")
        # B0004 等 not-found 业务码按查无处理
        if code == "B0004":
            raise BridgeNotFound(path)
        raise MCPError(
            "_bridge",
            path,
            f"bridge business error: code={code} message={envelope.get('message')}",
        )
    return envelope

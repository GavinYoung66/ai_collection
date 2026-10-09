"""统一 HTTP 指标采集与 /metrics 挂载。

- 纯 ASGI MetricsMiddleware（SHALL NOT 用 BaseHTTPMiddleware：其会缓冲 response body，
  破坏 mcp-gateway /mcp 的 SSE 流式响应），参考 mcp-gateway 既有 __call__ 模式下沉；
- mount_metrics(app, service_name)：注册 GET /metrics（prometheus 原生 exposition 格式，
  SHALL NOT 包装统一信封），并加挂 MetricsMiddleware；
- path 标签走白名单模板（app.routes 派生 + 静态基础项 + extra_path_templates），
  未命中兜底 __unmatched__，SHALL NOT 直接把原始请求路径写入标签（防高基数）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Iterable

from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# 导入即注册 6 个 LLM 指标到默认 REGISTRY（mount_metrics 暴露 /metrics 时一并输出）
from common.observability import llm_metrics as _llm_metrics

__all__ = [
    "UNMATCHED_PATH",
    "HTTPMetrics",
    "MetricsMiddleware",
    "build_http_metrics",
    "match_path_template",
    "mount_metrics",
]

# 未命中白名单的兜底 path 标签值
UNMATCHED_PATH = "__unmatched__"

# 静态基础白名单：管理端点 + 挂载子应用入口
# （mcp-gateway 的 /mcp 是 Mount 子应用内部路由，不出现在 app.routes 的 Route 中，需静态兜底）
_BASE_PATH_TEMPLATES: tuple[str, ...] = ("/health", "/metrics", "/mcp")


@dataclass(frozen=True)
class HTTPMetrics:
    """HTTP 三指标容器（svc 标签区分服务）。"""

    requests_total: Counter
    request_duration_seconds: Histogram
    requests_in_flight: Gauge


def build_http_metrics(registry: CollectorRegistry) -> HTTPMetrics:
    """在指定 registry 上构建并注册 HTTP 指标（自定义 registry 供测试隔离用）。"""
    return HTTPMetrics(
        requests_total=Counter(
            "http_requests_total",
            "Total HTTP requests",
            ["svc", "method", "path", "status"],
            registry=registry,
        ),
        request_duration_seconds=Histogram(
            "http_request_duration_seconds",
            "HTTP request latency in seconds",
            ["svc", "method", "path"],
            registry=registry,
        ),
        requests_in_flight=Gauge(
            "http_requests_in_flight",
            "In-flight HTTP requests",
            ["svc"],
            registry=registry,
        ),
    )


# 模块级默认实例：导入即注册到默认 REGISTRY（生产 mount_metrics 复用，重复 mount 不冲突）
DEFAULT_HTTP_METRICS = build_http_metrics(REGISTRY)


def _match_segments(t_segs: list[str], p_segs: list[str]) -> bool:
    """递归匹配模板段与请求段。

    - `{xxx}` 匹配任意**单段**；
    - `{xxx:path}`（Starlette path 转换器，正则 `.*`）匹配 **0 到多段**。
    """
    if not t_segs:
        return not p_segs
    head, rest = t_segs[0], t_segs[1:]
    if head.startswith("{") and head.endswith("}"):
        if head[1:-1].endswith(":path"):
            # 多段通配：贪心尝试各切分点
            return any(
                _match_segments(rest, p_segs[i:]) for i in range(len(p_segs) + 1)
            )
        if not p_segs:
            return False
        return _match_segments(rest, p_segs[1:])
    if not p_segs or p_segs[0] != head:
        return False
    return _match_segments(rest, p_segs[1:])


def match_path_template(path: str, templates: Iterable[str]) -> str:
    """把原始 path 映射到白名单模板；`{xxx}` 匹配单段、`{xxx:path}` 匹配多段，
    未命中返回 __unmatched__。"""
    p_segs = path.split("/")
    for template in templates:
        if _match_segments(template.split("/"), p_segs):
            return template
    return UNMATCHED_PATH


def _derive_path_templates(app: FastAPI) -> tuple[str, ...]:
    """从 app.routes 派生路由模板（仅取 Route；Mount 子应用经静态基础项/extra 覆盖）。"""
    templates = [r.path for r in app.routes if isinstance(r, Route)]
    return tuple(dict.fromkeys([*_BASE_PATH_TEMPLATES, *templates]))


class MetricsMiddleware:
    """纯 ASGI HTTP 指标中间件（scope/receive/send 三层签名）。

    - send_wrapper 拦截 http.response.start 取真实 status；
    - try/finally 保证 in_flight 回收（含异常路径；异常时 response start 未发出，status 按 500 计）；
    - 非 http scope（websocket/lifespan）直接透出不打指标。
    """

    def __init__(
        self,
        app: ASGIApp,
        service_name: str = "unknown",
        *,
        metrics: HTTPMetrics | None = None,
        path_templates: Iterable[str] | None = None,
        fastapi_app: FastAPI | None = None,
        extra_path_templates: Iterable[str] = (),
    ) -> None:
        self.asgi_app = app
        self.service_name = service_name
        self._m = metrics if metrics is not None else DEFAULT_HTTP_METRICS
        # path_templates 为显式覆盖（测试用）；未提供时在首次请求惰性派生
        self._templates_override = (
            tuple(path_templates) if path_templates is not None else None
        )
        self._fastapi_app = fastapi_app
        self._extra_templates = tuple(extra_path_templates)
        self._templates_cache: tuple[str, ...] | None = None

    def _resolve_templates(self) -> tuple[str, ...]:
        """首次请求时解析 path 白名单并缓存。

        惰性派生（而非 mount 时点急切派生）可消除「mount_metrics 早于业务路由注册」导致的
        业务路径全部落入 __unmatched__ 的静默降级——首次请求时 app.routes 必已全部注册。
        """
        if self._templates_cache is None:
            if self._templates_override is not None:
                base = self._templates_override
            elif self._fastapi_app is not None:
                base = _derive_path_templates(self._fastapi_app)
            else:
                base = _BASE_PATH_TEMPLATES
            self._templates_cache = tuple(
                dict.fromkeys([*base, *self._extra_templates])
            )
        return self._templates_cache

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.asgi_app(scope, receive, send)
            return
        status_holder = {"status": 500}
        start = time.perf_counter()

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        self._m.requests_in_flight.labels(self.service_name).inc()
        try:
            await self.asgi_app(scope, receive, send_wrapper)
        finally:
            self._m.requests_in_flight.labels(self.service_name).dec()
            path = match_path_template(scope.get("path", ""), self._resolve_templates())
            method = scope.get("method", "")
            self._m.requests_total.labels(
                self.service_name, method, path, str(status_holder["status"])
            ).inc()
            self._m.request_duration_seconds.labels(
                self.service_name, method, path
            ).observe(time.perf_counter() - start)


def mount_metrics(
    app: FastAPI,
    service_name: str,
    *,
    registry: CollectorRegistry | None = None,
    extra_path_templates: Iterable[str] = (),
    add_middleware: bool = True,
) -> None:
    """注册 GET /metrics 并加挂 MetricsMiddleware。

    - path 白名单由中间件在**首次请求**时从 app.routes 惰性派生（调用顺序无关）；
    - registry=None 用默认 REGISTRY（生产路径；HTTP/LLM 指标已随模块导入注册，一并暴露）；
      传自定义 registry 时在其上重建 HTTP 与 LLM 指标（测试隔离，互不污染）；
    - /metrics 返回 generate_latest 原生 exposition（Content-Type=text/plain; version=0.0.4），
      SHALL NOT 包装统一信封；
    - extra_path_templates 追加服务自定义白名单模板（如挂载子应用内部路径）。
    """
    if registry is None:
        registry = REGISTRY
        http_metrics = DEFAULT_HTTP_METRICS
    else:
        http_metrics = build_http_metrics(registry)
        # 自定义 registry 场景下 LLM 指标一并注册（默认 REGISTRY 已随模块导入注册）
        _llm_metrics.build_llm_metrics(registry)

    if add_middleware:
        app.add_middleware(
            MetricsMiddleware,
            service_name=service_name,
            metrics=http_metrics,
            fastapi_app=app,
            extra_path_templates=tuple(extra_path_templates),
        )

    @app.get("/metrics", include_in_schema=False)
    async def _metrics_endpoint() -> Response:
        # 显式 Content-Type header：starlette 0.27 对 text/* media_type 会无条件追加
        # '; charset=utf-8' 导致与 CONTENT_TYPE_LATEST 自带 charset 重复
        return Response(
            content=generate_latest(registry),
            headers={"Content-Type": CONTENT_TYPE_LATEST},
        )

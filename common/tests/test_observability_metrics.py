"""common.observability.metrics 单元测试：纯 ASGI 中间件 + mount_metrics + path 白名单。"""
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry

from common.observability.metrics import (
    UNMATCHED_PATH,
    match_path_template,
    mount_metrics,
)


def _make_app(registry: CollectorRegistry, service_name: str = "test-svc") -> FastAPI:
    """构造挂载指标的测试应用（含白名单内/外路由与异常路由），路由注册完再 mount。"""
    app = FastAPI()

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/api/v1/complaints/{id}")
    async def get_complaint(id: str):
        return {"id": id}

    @app.get("/boom")
    async def boom():
        raise RuntimeError("boom")

    mount_metrics(app, service_name, registry=registry)
    return app


class TestMatchPathTemplate:
    """path 白名单模板映射（1.4）。"""

    def test_literal_and_param_match(self):
        templates = ("/health", "/api/v1/complaints/{id}")
        assert match_path_template("/health", templates) == "/health"
        assert (
            match_path_template("/api/v1/complaints/abc", templates)
            == "/api/v1/complaints/{id}"
        )

    def test_unmatched_fallback(self):
        assert match_path_template("/nope/123", ("/health",)) == UNMATCHED_PATH
        # 段数不同不匹配（{xxx} 只匹配单段）
        assert (
            match_path_template(
                "/api/v1/complaints/1/extra", ("/api/v1/complaints/{id}",)
            )
            == UNMATCHED_PATH
        )

    def test_path_converter_multisegment(self):
        # {xxx:path}（Starlette path 转换器）匹配 0 到多段
        templates = ("/files/{sub:path}",)
        assert match_path_template("/files/a/b/c", templates) == "/files/{sub:path}"
        assert match_path_template("/files/a", templates) == "/files/{sub:path}"
        assert match_path_template("/files", templates) == "/files/{sub:path}"
        # 非 path 转换器的 {xxx} 仍严格单段
        assert (
            match_path_template("/files/a/b", ("/files/{sub}",)) == UNMATCHED_PATH
        )


class TestHTTPMetricLabelOrder:
    """HTTP 三指标标签名与顺序（防位置参数错位回归，对齐 spec 标签规范）。"""

    def test_labelnames_order(self):
        from common.observability.metrics import DEFAULT_HTTP_METRICS

        assert DEFAULT_HTTP_METRICS.requests_total._labelnames == (
            "svc",
            "method",
            "path",
            "status",
        )
        assert DEFAULT_HTTP_METRICS.request_duration_seconds._labelnames == (
            "svc",
            "method",
            "path",
        )
        assert DEFAULT_HTTP_METRICS.requests_in_flight._labelnames == ("svc",)


class TestMetricsMiddleware:
    """纯 ASGI 中间件行为（1.2）：status 拦截、in_flight 回收（含异常路径）。"""

    @pytest.fixture()
    def registry(self):
        # 独立 CollectorRegistry，避免默认 REGISTRY 全局状态串扰
        return CollectorRegistry()

    @pytest.fixture()
    def client(self, registry):
        # raise_server_exceptions=False：异常路径走 ServerErrorMiddleware 返回 500
        return TestClient(_make_app(registry), raise_server_exceptions=False)

    def test_metrics_endpoint_exposition(self, client):
        client.get("/health")
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == CONTENT_TYPE_LATEST
        # 原生 exposition 格式，SHALL NOT 包装统一信封
        assert not resp.text.lstrip().startswith("{")
        assert "http_requests_total" in resp.text

    def test_status_intercepted_and_duration_observed(self, client, registry):
        client.get("/health")
        assert (
            registry.get_sample_value(
                "http_requests_total",
                {
                    "svc": "test-svc",
                    "method": "GET",
                    "path": "/health",
                    "status": "200",
                },
            )
            == 1.0
        )
        assert (
            registry.get_sample_value(
                "http_request_duration_seconds_count",
                {"svc": "test-svc", "method": "GET", "path": "/health"},
            )
            == 1.0
        )

    def test_in_flight_recovered_on_success_and_exception(self, client, registry):
        client.get("/health")
        client.get("/boom")  # 异常路径
        # in_flight 在 try/finally 中回收（含异常路径）
        assert (
            registry.get_sample_value(
                "http_requests_in_flight", {"svc": "test-svc"}
            )
            == 0.0
        )
        # 异常时 http.response.start 未发出，status 按 500 计
        assert (
            registry.get_sample_value(
                "http_requests_total",
                {
                    "svc": "test-svc",
                    "method": "GET",
                    "path": "/boom",
                    "status": "500",
                },
            )
            == 1.0
        )

    def test_non_http_scope_passthrough(self, registry):
        app = FastAPI()

        @app.websocket("/ws")
        async def ws(websocket: WebSocket):
            await websocket.accept()
            await websocket.send_text("hi")
            await websocket.close()

        mount_metrics(app, "test-svc", registry=registry)
        client = TestClient(app)
        with client.websocket_connect("/ws") as conn:
            assert conn.receive_text() == "hi"
        # websocket 不产生任何 http 指标（未触碰 labels → 无样本）
        assert (
            registry.get_sample_value("http_requests_in_flight", {"svc": "test-svc"})
            is None
        )


class TestPathWhitelist:
    """path 高基数防护（1.4）：白名单模板映射 + __unmatched__ 兜底。"""

    @pytest.fixture()
    def registry(self):
        return CollectorRegistry()

    @pytest.fixture()
    def client(self, registry):
        return TestClient(_make_app(registry))

    def test_path_whitelist_collapses_ids(self, client, registry):
        for cid in ("111", "222", "333"):
            resp = client.get(f"/api/v1/complaints/{cid}")
            assert resp.status_code == 200
        # 3 次请求只产生 1 个时间序列，path 为模板而非原始值
        assert (
            registry.get_sample_value(
                "http_requests_total",
                {
                    "svc": "test-svc",
                    "method": "GET",
                    "path": "/api/v1/complaints/{id}",
                    "status": "200",
                },
            )
            == 3.0
        )
        # 原始 path SHALL NOT 进入标签
        for cid in ("111", "222", "333"):
            assert (
                registry.get_sample_value(
                    "http_requests_total",
                    {
                        "svc": "test-svc",
                        "method": "GET",
                        "path": f"/api/v1/complaints/{cid}",
                        "status": "200",
                    },
                )
                is None
            )

    def test_unmatched_path_fallback(self, client, registry):
        resp = client.get("/some/random/12345")
        assert resp.status_code == 404
        assert (
            registry.get_sample_value(
                "http_requests_total",
                {
                    "svc": "test-svc",
                    "method": "GET",
                    "path": UNMATCHED_PATH,
                    "status": "404",
                },
            )
            == 1.0
        )


class TestMountMetrics:
    def test_default_registry_reusable_across_apps(self):
        # 默认 REGISTRY 上重复 mount 不抛重复注册（复用模块级默认实例）
        app1, app2 = FastAPI(), FastAPI()
        mount_metrics(app1, "svc-a")
        mount_metrics(app2, "svc-b")
        assert TestClient(app1).get("/metrics").status_code == 200
        assert TestClient(app2).get("/metrics").status_code == 200

    def test_custom_registry_isolated_from_default(self):
        # 自定义 registry 的 /metrics 不输出默认 REGISTRY 的全局样本
        registry = CollectorRegistry()
        app = FastAPI()
        mount_metrics(app, "svc-iso", registry=registry)
        body = TestClient(app).get("/metrics").text
        assert 'svc="svc-iso"' in body
        assert 'svc="svc-a"' not in body

    def test_mount_before_routes_still_whitelisted(self):
        # 问题2 回归：mount_metrics 早于路由注册时，惰性派生仍能拿到模板
        registry = CollectorRegistry()
        app = FastAPI()
        mount_metrics(app, "svc-late", registry=registry)

        @app.get("/api/v1/late/{id}")
        async def late(id: str):
            return {"id": id}

        client = TestClient(app)
        for cid in ("1", "2"):
            assert client.get(f"/api/v1/late/{cid}").status_code == 200
        # 未落入 __unmatched__，而是折叠为单序列模板
        assert (
            registry.get_sample_value(
                "http_requests_total",
                {
                    "svc": "svc-late",
                    "method": "GET",
                    "path": "/api/v1/late/{id}",
                    "status": "200",
                },
            )
            == 2.0
        )
        assert (
            registry.get_sample_value(
                "http_requests_total",
                {
                    "svc": "svc-late",
                    "method": "GET",
                    "path": UNMATCHED_PATH,
                    "status": "200",
                },
            )
            is None
        )

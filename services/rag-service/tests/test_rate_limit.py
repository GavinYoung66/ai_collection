"""接口限流组件测试：未触发放行 / 达到阈值 429 / 用户维度隔离 / Redis 故障降级。"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.events.resilience import clear_emitted, emitted, on_resilience_event
from app.services import rate_limit
from common.config.settings import settings
from common.resilience import hooks
from common.security.rbac import create_token
from common.trace.middleware import TraceMiddleware


class FakeRedis:
    """内存版 Redis mock：SET NX EX + incr（common allow 崩溃安全配方）。"""

    def __init__(self, fail=False):
        self.store: dict = {}
        self.fail = fail

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("redis down")

    async def set(self, key, value, ex=None, nx=False):  # noqa: ARG002
        self._maybe_fail()
        if nx and key in self.store:
            return None
        self.store[key] = int(value)
        return True

    async def incr(self, key):
        self._maybe_fail()
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    async def expire(self, key, ttl):
        self._maybe_fail()


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rate_limit, "_redis", fake)
    return fake


@pytest.fixture
def client(monkeypatch):
    """最小化测试 app：仅挂载限流依赖 + 429 handler + 追踪中间件，不触碰业务接口。"""
    # T68：代理头仅在 TRUST_PROXY_HEADERS=true 时被采信——对齐网关真实部署拓扑
    #（nginx 已 proxy_set_header X-Real-IP），否则本环境 request.client 为 None 落 anonymous。
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    app = FastAPI()
    app.add_middleware(TraceMiddleware)
    rate_limit.register_rate_limit_handler(app)

    @app.get("/test")
    async def test_route(rate: None = Depends(rate_limit.rate_limiter("test_bucket", 2))):
        return {"ok": True}

    # 显式注入 X-Real-IP：TestClient 不提供 ASGI client 地址（request.client is None），
    # 网关真实部署也以 X-Real-IP 为准，避免用例依赖 TestClient 内部实现。
    return TestClient(app, headers={"X-Real-IP": "10.0.0.1"})


class TestRateLimiter:
    def test_under_limit_passes(self, client, fake_redis):
        """limit=2：前 2 次请求正常放行。"""
        for _ in range(2):
            resp = client.get("/test")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        # T68：匿名身份落 ip: 命名空间（fixture 注入 X-Real-IP 且 TRUST_PROXY_HEADERS=true）
        assert fake_redis.store["rag:ratelimit:test_bucket:ip:10.0.0.1"] == 2

    def test_over_limit_emits_hit_with_trace_id(self, client, fake_redis):
        """超限发 rag.rate_limit.hit，payload 含请求 trace_id。"""
        clear_emitted()
        hooks.set_publisher(on_resilience_event)
        try:
            headers = {"X-Trace-Id": "trace-rag-rl"}
            for _ in range(2):
                assert client.get("/test", headers=headers).status_code == 200
            resp = client.get("/test", headers=headers)
            assert resp.status_code == 429
            assert resp.json()["code"] == "B0006"
            hits = [payload for name, payload in emitted if name == "rag.rate_limit.hit"]
            assert hits
            assert hits[0]["trace_id"] == "trace-rag-rl"
            assert hits[0]["key"] == "rag:ratelimit:test_bucket:ip:10.0.0.1"
            assert hits[0]["reason"] == "rate_limited"
        finally:
            hooks.set_publisher(None)
            clear_emitted()

    def test_over_limit_returns_429(self, client, fake_redis):
        """limit=2：第 3 次请求返回 429 + 统一响应包（B0006）。"""
        for _ in range(2):
            assert client.get("/test").status_code == 200

        resp = client.get("/test")
        assert resp.status_code == 429
        body = resp.json()
        assert body["code"] == "B0006"
        assert body["message"] == "请求过于频繁，请稍后再试"
        assert body["data"] is None
        assert body["trace_id"]

    def test_user_dimension_isolation(self, client, fake_redis):
        """不同 token 身份（uid: 命名空间）独立计数，互不影响。

        T68：X-User-Id 请求头不再是限流维度（伪造可膨胀 key 空间）；
        身份隔离改由验签后的 sub（uid: 命名空间）承担。
        """
        tok_a = create_token({"sub": "user_a", "role": "admin"})
        tok_b = create_token({"sub": "user_b", "role": "admin"})
        # user_a 连续 3 次：2 次放行 + 1 次 429
        assert client.get("/test", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 200
        assert client.get("/test", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 200
        assert client.get("/test", headers={"Authorization": f"Bearer {tok_a}"}).status_code == 429
        # user_b 独立计数，不受 user_a 达阈值影响
        assert client.get("/test", headers={"Authorization": f"Bearer {tok_b}"}).status_code == 200
        assert client.get("/test", headers={"Authorization": f"Bearer {tok_b}"}).status_code == 200
        assert client.get("/test", headers={"Authorization": f"Bearer {tok_b}"}).status_code == 429
        # 计数键按身份隔离（uid: 命名空间）
        assert fake_redis.store["rag:ratelimit:test_bucket:uid:user_a"] == 3
        assert fake_redis.store["rag:ratelimit:test_bucket:uid:user_b"] == 3

    def test_redis_failure_passes(self, client, monkeypatch):
        """Redis 故障静默放行，不返回 500。"""
        monkeypatch.setattr(rate_limit, "_redis", FakeRedis(fail=True))
        for _ in range(3):
            resp = client.get("/test")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

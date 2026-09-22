"""接口限流组件测试：未触发放行 / 达到阈值 429 / 用户维度隔离 / Redis 故障降级。"""
import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.events.resilience import clear_emitted, emitted, on_resilience_event
from app.services import rate_limit
from common.resilience import hooks
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
def client():
    """最小化测试 app：仅挂载限流依赖 + 429 handler + 追踪中间件，不触碰业务接口。"""
    app = FastAPI()
    app.add_middleware(TraceMiddleware)
    rate_limit.register_rate_limit_handler(app)

    @app.get("/test")
    async def test_route(rate: None = Depends(rate_limit.rate_limiter("test_bucket", 2))):
        return {"ok": True}

    return TestClient(app)


class TestRateLimiter:
    def test_under_limit_passes(self, client, fake_redis):
        """limit=2：前 2 次请求正常放行。"""
        for _ in range(2):
            resp = client.get("/test")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}
        assert fake_redis.store["rag:ratelimit:test_bucket:testclient"] == 2

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
            assert hits[0]["key"] == "rag:ratelimit:test_bucket:testclient"
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
        """不同 X-User-Id 独立计数，互不影响。"""
        # user_a 连续 3 次：2 次放行 + 1 次 429
        assert client.get("/test", headers={"X-User-Id": "user_a"}).status_code == 200
        assert client.get("/test", headers={"X-User-Id": "user_a"}).status_code == 200
        assert client.get("/test", headers={"X-User-Id": "user_a"}).status_code == 429
        # user_b 独立计数，不受 user_a 达阈值影响
        assert client.get("/test", headers={"X-User-Id": "user_b"}).status_code == 200
        assert client.get("/test", headers={"X-User-Id": "user_b"}).status_code == 200
        assert client.get("/test", headers={"X-User-Id": "user_b"}).status_code == 429
        # 计数键按用户隔离
        assert fake_redis.store["rag:ratelimit:test_bucket:user_a"] == 3
        assert fake_redis.store["rag:ratelimit:test_bucket:user_b"] == 3

    def test_redis_failure_passes(self, client, monkeypatch):
        """Redis 故障静默放行，不返回 500。"""
        monkeypatch.setattr(rate_limit, "_redis", FakeRedis(fail=True))
        for _ in range(3):
            resp = client.get("/test")
            assert resp.status_code == 200
            assert resp.json() == {"ok": True}

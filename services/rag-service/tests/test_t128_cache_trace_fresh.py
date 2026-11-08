"""T128：缓存命中返回陈旧 trace_id 修复测试。

spec rag-service/hybrid-search「缓存键语义完整性与 trace 一致性」验收：
命中缓存时响应 trace_id 等于本次请求的追踪标识（X-Trace-Id），
可在本次日志链路中检索到。

缓存值结构（query_cache.set_cached 存完整对外响应 dict）：
{"code", "message", "data", "trace_id"}——修复采用最小改动裁决：
写入端结构不变（兼容 TTL 内存量旧条目），命中出口将 trace_id
覆盖为本次 get_trace_id()，业务字段原样透传。
"""
import json
import uuid

import pytest

from app.api import search as search_api
from app.models.search import SearchRequest
from app.services import query_cache
from app.settings import settings
from common.trace.middleware import get_trace_id, set_trace_id


class FakeRedis:
    """内存版 Redis mock（对齐 test_query_cache 形态）。"""

    def __init__(self):
        self.store: dict = {}
        self.sets: dict = {}
        self.ttls: dict = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value
        self.ttls[key] = ttl

    async def sadd(self, key, *members):
        self.sets.setdefault(key, set()).update(members)

    async def smembers(self, key):
        return set(self.sets.get(key, set()))

    async def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
            self.sets.pop(k, None)


class _EmptyScalars:
    def all(self):
        return []


class MockResult:
    def scalars(self):
        return _EmptyScalars()

    def all(self):
        return [(
            uuid.UUID(_MOCK_DOC_ID),
            "催收制度",
            None,
            None,
            None,
            None,
            None,
        )]

    def scalar_one_or_none(self):
        return None

    @property
    def rowcount(self):
        return 0


class MockDB:
    async def execute(self, stmt):
        return MockResult()

    def add(self, obj):
        pass

    async def commit(self):
        pass

    async def rollback(self):
        pass


class CapturingDB(MockDB):
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)


class MockMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        return [{"chunk_id": _MOCK_CHUNK_ID, "doc_id": _MOCK_DOC_ID, "category": "regulation", "score": 0.9}]


class MockES:
    async def search(self, query, top_n=20, category=None):
        return [{"chunk_id": _MOCK_CHUNK_ID, "doc_id": _MOCK_DOC_ID, "category": "regulation", "content": "内容1", "score": 5.0}]


class MockEmbed:
    def __init__(self):
        self.calls = 0

    async def embed(self, text):
        self.calls += 1
        return [0.1] * 1024

    async def batch_embed(self, texts):
        self.calls += 1
        return [[0.1] * 1024 for _ in texts]


class MockRerank:
    async def is_degraded(self):
        return False

    async def rerank(self, query, candidates):
        return [{"chunk_id": c["chunk_id"], "rerank_score": 0.9} for c in candidates]


_MOCK_DOC_ID = str(uuid.uuid4())
_MOCK_CHUNK_ID = str(uuid.uuid4())


def _route_args(embed, db=None):
    """构造 hybrid_search 路由参数（V2/V5 服务开关全关传 None 安全）。"""
    from starlette.datastructures import Headers

    class MockRequest:
        headers = Headers({})

    class MockResponse:
        def __init__(self):
            self.headers = {}

    return {
        "request": MockRequest(),
        "response": MockResponse(),
        "db": db or MockDB(),
        "milvus": MockMilvus(),
        "es": MockES(),
        "embed": embed,
        "rerank": MockRerank(),
        "rewrite_svc": None,
        "routing_svc": None,
        "weighting_svc": None,
        "row_access_svc": None,
        "session_svc": None,
        "llm_svc": None,
    }


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(query_cache, "_redis", fake)
    return fake


@pytest.fixture
def cache_enabled(monkeypatch):
    monkeypatch.setattr(settings, "KB_CACHE_ENABLED", True)


@pytest.fixture
def trace_ctx():
    """设置本次请求 trace_id（模拟 TraceMiddleware 从 X-Trace-Id 落 contextvar）；
    用例结束恢复空串，防 contextvar 泄漏到其他用例。"""
    def _set(trace_id: str) -> str:
        set_trace_id(trace_id)
        return trace_id

    yield _set
    set_trace_id("")


class TestCacheHitTraceFresh:
    @pytest.mark.asyncio
    async def test_hit_returns_second_request_trace_id(
        self, monkeypatch, fake_redis, cache_enabled, trace_ctx
    ):
        """两次不同 trace_id 请求同一 query：第二次命中缓存，响应 trace_id
        为第二次请求的值且 != 缓存写入时的旧值。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)

        # 第一次请求：未命中 → 检索 → 写缓存（trace_id=trace-req-1）
        trace_ctx("trace-req-1")
        first = await search_api.hybrid_search(req, **_route_args(MockEmbed()))
        assert first["trace_id"] == "trace-req-1"

        # 第二次请求：命中缓存（trace_id=trace-req-2）
        trace_ctx("trace-req-2")
        second = await search_api.hybrid_search(req, **_route_args(MockEmbed()))
        assert second["trace_id"] == "trace-req-2"
        assert second["trace_id"] != "trace-req-1"
        assert second["trace_id"] == get_trace_id()

    @pytest.mark.asyncio
    async def test_cache_miss_trace_id_unchanged(
        self, monkeypatch, fake_redis, cache_enabled, trace_ctx
    ):
        """未命中路径 trace_id 语义不变：响应 trace_id 即本次请求 contextvar。"""
        trace_ctx("trace-fresh")
        resp = await search_api.hybrid_search(
            SearchRequest(query="未命中查询", category="regulation", top_k=5),
            **_route_args(MockEmbed()),
        )
        assert resp["trace_id"] == "trace-fresh"
        assert resp["trace_id"] == get_trace_id()

    @pytest.mark.asyncio
    async def test_hit_business_result_intact(
        self, monkeypatch, fake_redis, cache_enabled, trace_ctx
    ):
        """缓存命中业务结果完整：除 trace_id 外与写入响应逐字段一致。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        req = SearchRequest(query="业务完整查询", category="regulation", top_k=5)

        trace_ctx("trace-write")
        first = await search_api.hybrid_search(req, **_route_args(MockEmbed()))
        trace_ctx("trace-read")
        second = await search_api.hybrid_search(req, **_route_args(MockEmbed()))

        assert second["code"] == first["code"]
        assert second["message"] == first["message"]
        assert second["data"] == first["data"]
        assert second["data"]["results"], "mock 检索应返回非空 results"
        assert second["trace_id"] == "trace-read"
        assert first["trace_id"] == "trace-write"

    @pytest.mark.asyncio
    async def test_hit_log_trace_matches_response(
        self, monkeypatch, fake_redis, cache_enabled, trace_ctx
    ):
        """缓存命中落日志的 trace_id 与响应 trace_id 一致（本次请求链路可检索）。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        req = SearchRequest(query="日志一致查询", category="regulation", top_k=5)

        trace_ctx("trace-log-write")
        await search_api.hybrid_search(req, **_route_args(MockEmbed()))

        db = CapturingDB()
        trace_ctx("trace-log-hit")
        resp = await search_api.hybrid_search(req, **_route_args(MockEmbed(), db=db))

        assert len(db.added) == 1
        entry = db.added[0]
        assert entry.cache_hit == "1"
        assert entry.trace_id == "trace-log-hit"
        assert entry.trace_id == resp["trace_id"]

    @pytest.mark.asyncio
    async def test_hit_does_not_mutate_stored_cache_value(
        self, monkeypatch, fake_redis, cache_enabled, trace_ctx
    ):
        """命中覆盖仅作用于响应副本：Redis 存储值不被污染（trace_id 保持写入时值）。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        req = SearchRequest(query="存储不变查询", category="regulation", top_k=5)
        key = query_cache.make_cache_key(req.query, req.category, req.top_k, scope="anon")

        trace_ctx("trace-store-1")
        await search_api.hybrid_search(req, **_route_args(MockEmbed()))
        stored_before = json.loads(fake_redis.store[key])
        assert stored_before["trace_id"] == "trace-store-1"

        trace_ctx("trace-store-2")
        await search_api.hybrid_search(req, **_route_args(MockEmbed()))
        stored_after = json.loads(fake_redis.store[key])

        assert stored_after == stored_before
        assert stored_after["trace_id"] == "trace-store-1"

    @pytest.mark.asyncio
    async def test_cached_value_structure_complete(
        self, monkeypatch, fake_redis, cache_enabled, trace_ctx
    ):
        """缓存值结构断言：写入的缓存条目含完整对外响应四字段结构。"""
        monkeypatch.setattr(settings, "KB_SUMMARY_ENABLED", False)
        req = SearchRequest(query="结构断言查询", category="regulation", top_k=5)
        key = query_cache.make_cache_key(req.query, req.category, req.top_k, scope="anon")

        trace_ctx("trace-struct")
        resp = await search_api.hybrid_search(req, **_route_args(MockEmbed()))
        assert key in fake_redis.store

        stored = json.loads(fake_redis.store[key])
        assert set(stored.keys()) >= {"code", "message", "data", "trace_id"}
        assert stored["trace_id"] == "trace-struct"
        assert stored["data"]["results"] == resp["data"]["results"]
        assert fake_redis.ttls[key] == settings.KB_CACHE_TTL
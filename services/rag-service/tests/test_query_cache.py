"""查询缓存切面测试：命中/未命中/开关关闭/Redis 故障降级/文档操作失效。"""
import json
import uuid

import pytest

from app.api import documents as documents_api
from app.api import search as search_api
from app.models.search import SearchRequest
from app.services import query_cache
from app.settings import settings


class FakeRedis:
    """内存版 Redis mock，可模拟故障。"""

    def __init__(self, fail=False):
        self.store: dict = {}
        self.sets: dict = {}
        self.ttls: dict = {}
        self.fail = fail
        self.calls = {"get": 0, "setex": 0, "sadd": 0, "smembers": 0, "delete": 0}

    def _maybe_fail(self):
        if self.fail:
            raise ConnectionError("redis down")

    async def get(self, key):
        self.calls["get"] += 1
        self._maybe_fail()
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.calls["setex"] += 1
        self._maybe_fail()
        self.store[key] = value
        self.ttls[key] = ttl

    async def sadd(self, key, *members):
        self.calls["sadd"] += 1
        self._maybe_fail()
        self.sets.setdefault(key, set()).update(members)

    async def smembers(self, key):
        self.calls["smembers"] += 1
        self._maybe_fail()
        return set(self.sets.get(key, set()))

    async def delete(self, *keys):
        self.calls["delete"] += 1
        self._maybe_fail()
        for k in keys:
            self.store.pop(k, None)
            self.sets.pop(k, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(query_cache, "_redis", fake)
    return fake


@pytest.fixture
def cache_enabled(monkeypatch):
    monkeypatch.setattr(settings, "KB_CACHE_ENABLED", True)


_MOCK_DOC_ID = str(uuid.uuid4())
_MOCK_CHUNK_ID = str(uuid.uuid4())


# ---------- 单元：key 生成与读写/失效 ----------

class TestQueryCacheUnit:
    def test_make_cache_key_format(self):
        key = query_cache.make_cache_key("逾期协商", "regulation", 5)
        # V2: key 前缀升级为 rag:cache:query:v2:{md5}（含 flags_hash 隔离）
        assert key.startswith("rag:cache:query:v2:")
        assert len(key) == len("rag:cache:query:v2:") + 32
        assert key == query_cache.make_cache_key("逾期协商", "regulation", 5)
        assert key != query_cache.make_cache_key("逾期协商", "regulation", 3)
        assert key != query_cache.make_cache_key("逾期协商", None, 5)

    @pytest.mark.asyncio
    async def test_get_cached_hit(self, fake_redis):
        fake_redis.store["k1"] = json.dumps({"code": "0", "data": None})
        assert await query_cache.get_cached("k1") == {"code": "0", "data": None}

    @pytest.mark.asyncio
    async def test_get_cached_miss(self, fake_redis):
        assert await query_cache.get_cached("nope") is None

    @pytest.mark.asyncio
    async def test_get_cached_redis_fail_silent(self):
        fake = FakeRedis(fail=True)
        query_cache._redis = fake
        try:
            assert await query_cache.get_cached("k1") is None
        finally:
            query_cache._redis = None

    @pytest.mark.asyncio
    async def test_set_cached_ttl_and_index(self, fake_redis):
        resp = {"code": "0", "message": "success", "data": {"results": []}, "trace_id": "t"}
        await query_cache.set_cached("k1", resp, "regulation")
        assert json.loads(fake_redis.store["k1"]) == resp
        assert fake_redis.ttls["k1"] == settings.KB_CACHE_TTL
        assert "k1" in fake_redis.sets["rag:cache:keys:regulation"]

    @pytest.mark.asyncio
    async def test_set_cached_redis_fail_silent(self):
        fake = FakeRedis(fail=True)
        query_cache._redis = fake
        try:
            await query_cache.set_cached("k1", {"code": "0"}, "regulation")
        finally:
            query_cache._redis = None

    @pytest.mark.asyncio
    async def test_invalidate_category(self, fake_redis):
        fake_redis.store["k1"] = "x"
        fake_redis.store["k2"] = "y"
        fake_redis.store["kg"] = "g"
        fake_redis.sets["rag:cache:keys:regulation"] = {"k1", "k2"}
        fake_redis.sets["rag:cache:keys:"] = {"kg"}
        await query_cache.invalidate_category("regulation")
        assert fake_redis.store == {}
        assert fake_redis.sets == {}

    @pytest.mark.asyncio
    async def test_invalidate_redis_fail_silent(self):
        fake = FakeRedis(fail=True)
        query_cache._redis = fake
        try:
            await query_cache.invalidate_category("regulation")
        finally:
            query_cache._redis = None


# ---------- 路由级：search 缓存切面 ----------

class _EmptyScalars:
    def all(self):
        return []


class MockResult:
    def scalars(self):
        # content/chunk_index 补齐走 scalars().all()；检索内容来自 ES mock
        return _EmptyScalars()

    def all(self):
        # 同一 mock 行同时满足 get_active_doc_id_set / get_titles_map / get_meta_dict
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


class MockMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        return [{"chunk_id": _MOCK_CHUNK_ID, "doc_id": _MOCK_DOC_ID, "category": "regulation", "score": 0.9}]

    async def delete_by_doc(self, doc_id):
        pass


class MockES:
    async def search(self, query, top_n=20, category=None):
        return [{"chunk_id": _MOCK_CHUNK_ID, "doc_id": _MOCK_DOC_ID, "category": "regulation", "content": "内容1", "score": 5.0}]

    async def delete_by_doc(self, doc_id):
        pass


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


def _route_args(embed):
    """构造 hybrid_search 路由参数（含 V2 服务 mock，开关全关时传 None 安全）。"""
    from starlette.datastructures import Headers

    class MockRequest:
        headers = Headers({})

    class MockResponse:
        def __init__(self):
            self.headers = {}

    return {
        "request": MockRequest(),
        "response": MockResponse(),
        "db": MockDB(),
        "milvus": MockMilvus(),
        "es": MockES(),
        "embed": embed,
        "rerank": MockRerank(),
        # V2 服务（开关全关，传 None 不触发调用）
        "rewrite_svc": None,
        "routing_svc": None,
        "weighting_svc": None,
        "row_access_svc": None,
        "session_svc": None,
        # V5 服务（开关全关，传 None 不触发调用；缺失时 FastAPI Depends 对象不可调用）
        "llm_svc": None,
    }


class TestSearchRouteCache:
    @pytest.mark.asyncio
    async def test_cache_hit_returns_cached_and_skips_service(
        self, monkeypatch, fake_redis, cache_enabled
    ):
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)
        key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        cached = {"code": "0", "message": "success", "data": {"results": []}, "trace_id": "old"}
        fake_redis.store[key] = json.dumps(cached, ensure_ascii=False)

        embed = MockEmbed()
        resp = await search_api.hybrid_search(req, **_route_args(embed))
        assert resp == cached
        assert embed.calls == 0  # 命中缓存不执行检索

    @pytest.mark.asyncio
    async def test_cache_miss_executes_and_writes(
        self, monkeypatch, fake_redis, cache_enabled
    ):
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)
        embed = MockEmbed()
        resp = await search_api.hybrid_search(req, **_route_args(embed))
        assert resp["code"] == "0"
        assert embed.calls == 1

        key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        assert key in fake_redis.store
        assert json.loads(fake_redis.store[key]) == resp
        assert fake_redis.ttls[key] == settings.KB_CACHE_TTL
        assert key in fake_redis.sets["rag:cache:keys:regulation"]

    @pytest.mark.asyncio
    async def test_disabled_skips_cache(self, monkeypatch, fake_redis):
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", False)
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)
        embed = MockEmbed()
        resp = await search_api.hybrid_search(req, **_route_args(embed))
        assert resp["code"] == "0"
        assert embed.calls == 1
        assert sum(fake_redis.calls.values()) == 0  # 开关关闭完全跳过缓存

    @pytest.mark.asyncio
    async def test_redis_failure_degrades(self, monkeypatch, cache_enabled):
        fake = FakeRedis(fail=True)
        monkeypatch.setattr(query_cache, "_redis", fake)
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)
        embed = MockEmbed()
        resp = await search_api.hybrid_search(req, **_route_args(embed))
        assert resp["code"] == "0"  # Redis 故障不抛异常，正常返回
        assert embed.calls == 1


# ---------- V1-T19/T20 整改测试：兜底不缓存 + 命中落日志 ----------

class TestCacheFallbackAndHitLog:
    def test_is_cacheable_unit(self):
        """is_cacheable：降级/兜底响应不缓存，正常响应可缓存。"""
        assert query_cache.is_cacheable(
            {"results": [{"doc_id": "d"}], "degraded": False, "message": None}
        ) is True
        assert query_cache.is_cacheable(
            {"results": [], "degraded": True, "message": None}
        ) is False  # 降级（单路召回）
        assert query_cache.is_cacheable(
            {"results": [], "degraded": False, "message": "知识库暂无该业务"}
        ) is False  # 无匹配兜底

    @pytest.mark.asyncio
    async def test_fallback_response_not_cached(
        self, monkeypatch, fake_redis, cache_enabled
    ):
        """兜底响应（rerank 0.1 < 0.35）不写缓存（V1-T19）。"""
        req = SearchRequest(query="无匹配查询", category="regulation", top_k=5)

        class LowScoreRerank:
            async def is_degraded(self):
                return False

            async def rerank(self, query, candidates):
                return [
                    {"chunk_id": c["chunk_id"], "rerank_score": 0.1}
                    for c in candidates
                ]

        embed = MockEmbed()
        args = _route_args(embed)
        args["rerank"] = LowScoreRerank()
        resp = await search_api.hybrid_search(req, **args)
        assert resp["code"] == "0"
        assert resp["data"]["results"] == []  # 兜底响应

        key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        assert key not in fake_redis.store  # 兜底结果未被缓存
        assert fake_redis.calls["setex"] == 0

    @pytest.mark.asyncio
    async def test_degraded_response_not_cached(
        self, monkeypatch, fake_redis, cache_enabled
    ):
        """降级响应（Milvus 宕机 ES-only）不写缓存（V1-T19）。"""
        req = SearchRequest(query="降级查询", category="regulation", top_k=5)

        class MilvusDown:
            async def search(self, query_vector, top_n=20, category=None):
                raise ConnectionError("milvus down")

            async def delete_by_doc(self, doc_id):
                pass

        embed = MockEmbed()
        args = _route_args(embed)
        args["milvus"] = MilvusDown()
        resp = await search_api.hybrid_search(req, **args)
        assert resp["code"] == "0"
        assert resp["data"]["degraded"] is True

        key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        assert key not in fake_redis.store  # 降级结果未被缓存

    @pytest.mark.asyncio
    async def test_cache_hit_logs_cache_hit_entry(
        self, monkeypatch, fake_redis, cache_enabled
    ):
        """缓存命中落 kb_search_logs（cache_hit='1'）（V1-T20）。"""
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)
        key = query_cache.make_cache_key(req.query, req.category, req.top_k)
        cached = {
            "code": "0",
            "message": "success",
            "data": {"results": []},
            "trace_id": "old",
        }
        fake_redis.store[key] = json.dumps(cached, ensure_ascii=False)

        class CapturingDB(MockDB):
            def __init__(self):
                self.added = []

            def add(self, obj):
                self.added.append(obj)

        db = CapturingDB()
        args = _route_args(MockEmbed())
        args["db"] = db
        resp = await search_api.hybrid_search(req, **args)
        assert resp == cached
        assert len(db.added) == 1
        entry = db.added[0]
        assert entry.cache_hit == "1"
        assert entry.query == "逾期协商"
        assert entry.category == "regulation"


# ---------- 路由级：文档操作触发缓存失效 ----------

class MockDoc:
    def __init__(self, category="regulation"):
        self.id = uuid.uuid4()
        self.category = category


class MockDocDB(MockDB):
    def __init__(self, doc):
        self.doc = doc

    async def execute(self, stmt):
        result = MockResult()
        result.scalar_one_or_none = lambda: self.doc
        return result


class TestDocumentInvalidationHook:
    @pytest.mark.asyncio
    async def test_delete_route_invalidates_cache(
        self, monkeypatch, fake_redis, cache_enabled
    ):
        key = "rag:cache:query:abc"
        fake_redis.store[key] = "x"
        fake_redis.sets["rag:cache:keys:regulation"] = {key}

        doc = MockDoc("regulation")
        async def _get_embed():
            return MockEmbed()

        monkeypatch.setattr(documents_api, "get_milvus_client", lambda: MockMilvus())
        monkeypatch.setattr(documents_api, "get_es_client", lambda: MockES())
        monkeypatch.setattr(documents_api, "get_embed_client", _get_embed)

        resp = await documents_api.delete_document(str(doc.id), db=MockDocDB(doc))
        assert resp["code"] == "0"
        assert fake_redis.store == {}  # 删除文档后该 category 缓存已失效

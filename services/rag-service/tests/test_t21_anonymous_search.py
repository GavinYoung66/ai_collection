"""T21：RAG /search 匿名请求不放行受限文档。

覆盖：
- 正常：匿名（无 JWT）检索仅返回 KB_ANONYMOUS_CATEGORIES 白名单（默认 regulation）
- 边界：白名单为空 → 匿名零结果；白名单多类别；category 显式指向受限类别 → 空
- 异常：行级权限服务抛异常 → 匿名仍按静态白名单收紧，不回到全量放行
- 缓存：匿名与登录用户缓存 key 隔离，受限文档不会经由缓存泄漏给匿名请求
"""
import json

import pytest
from structlog.testing import capture_logs

from app.api import search as search_api
from app.models.search import SearchRequest
from app.services import query_cache
from app.services.row_access_service import (
    RowAccessService,
    anonymous_allowed_categories,
    filter_by_categories,
)
from app.services.search_service import SearchService
from app.settings import settings
from tests.test_query_cache import FakeRedis, MockEmbed, _route_args


class MockLogService:
    async def log(self, **kwargs):
        pass


class MockRerank:
    async def is_degraded(self):
        return False

    async def rerank(self, query, candidates):
        return [{"chunk_id": c["chunk_id"], "rerank_score": 0.9} for c in candidates]


class MixedMilvus:
    """混合类别召回：regulation + script + case。"""

    async def search(self, query_vector, top_n=20, category=None):
        return [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation", "score": 0.9},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script", "score": 0.8},
            {"chunk_id": "c3", "doc_id": "d3", "category": "case", "score": 0.7},
        ]


class MixedES:
    async def search(self, query, top_n=20, category=None):
        return [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation",
             "content": "制度内容", "score": 5.0},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script",
             "content": "话术内容", "score": 4.0},
            {"chunk_id": "c3", "doc_id": "d3", "category": "case",
             "content": "案例内容", "score": 3.0},
        ]


class RaisingRowAccess:
    """匿名过滤路径抛异常（验证异常兜底仍按静态白名单收紧）。"""

    def filter(self, items, jwt_payload, category_filter=None):
        raise RuntimeError("matrix load error")

    def get_default_matrix(self):
        return {("default", "default"): ["regulation", "script", "case", "other"]}


def _make_svc(row_access_svc=None):
    return SearchService(
        milvus_client=MixedMilvus(),
        es_client=MixedES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
        doc_repo=None,
        row_access_service=row_access_svc,
    )


class TestAnonymousFilterServiceLevel:
    @pytest.mark.asyncio
    async def test_anonymous_only_whitelisted_category(self):
        """正常：匿名检索（category=None）只返回 regulation，受限类别被剔除。"""
        svc = _make_svc()
        result = await svc.hybrid_search("测试", None, 5)
        assert [r["doc_id"] for r in result["results"]] == ["d1"]

    @pytest.mark.asyncio
    async def test_anonymous_with_row_access_service(self):
        """正常：注入 RowAccessService 时匿名分支同样只放行白名单并记录审计日志。"""
        svc = _make_svc(RowAccessService())
        with capture_logs() as cap:
            result = await svc.hybrid_search("测试", None, 5)
        assert [r["doc_id"] for r in result["results"]] == ["d1"]
        assert any(e.get("event") == "row_access_anonymous_filtered" for e in cap)

    @pytest.mark.asyncio
    async def test_empty_whitelist_fails_closed(self, monkeypatch):
        """边界：白名单为空 → 匿名零结果（fail-closed）。"""
        monkeypatch.setattr(settings, "KB_ANONYMOUS_CATEGORIES", "")
        svc = _make_svc()
        result = await svc.hybrid_search("测试", None, 5)
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_multiple_whitelisted_categories(self, monkeypatch):
        """边界：白名单含多类别 → 按配置放行 regulation + case。"""
        monkeypatch.setattr(settings, "KB_ANONYMOUS_CATEGORIES", "regulation,case")
        svc = _make_svc()
        result = await svc.hybrid_search("测试", None, 5)
        assert [r["doc_id"] for r in result["results"]] == ["d1", "d3"]

    @pytest.mark.asyncio
    async def test_anonymous_explicit_restricted_category_empty(self):
        """边界：匿名显式请求受限类别（script）→ 白名单交集为空，零结果。"""
        svc = _make_svc()
        result = await svc.hybrid_search("测试", "script", 5)
        assert result["results"] == []

    @pytest.mark.asyncio
    async def test_anonymous_filter_exception_still_restricted(self):
        """异常：行级服务抛异常 → 匿名仍按静态白名单收紧，不返回 script/case。"""
        svc = _make_svc(RaisingRowAccess())
        with capture_logs() as cap:
            result = await svc.hybrid_search("测试", None, 5)
        assert [r["doc_id"] for r in result["results"]] == ["d1"]
        assert any(e.get("event") == "row_access_unexpected_error" for e in cap)

    @pytest.mark.asyncio
    async def test_authenticated_not_restricted_when_row_level_off(self):
        """对照：登录用户 + 行级开关关闭 → 全类别放行（存量行为不变）。"""
        svc = _make_svc(RowAccessService())
        result = await svc.hybrid_search(
            "测试", None, 5, jwt_payload={"role": "AGENT", "department": "催收一部"}
        )
        assert {r["doc_id"] for r in result["results"]} == {"d1", "d2", "d3"}


class TestAnonymousWhitelistConfig:
    def test_default_is_regulation(self):
        assert anonymous_allowed_categories() == {"regulation"}

    def test_illegal_values_ignored(self, monkeypatch):
        monkeypatch.setattr(settings, "KB_ANONYMOUS_CATEGORIES", "regulation,bogus, case")
        assert anonymous_allowed_categories() == {"regulation", "case"}

    def test_category_filter_intersection(self):
        items = [
            {"chunk_id": "c1", "category": "regulation"},
            {"chunk_id": "c2", "category": "case"},
        ]
        assert filter_by_categories(items, {"regulation", "case"}, "case") == [items[1]]


class TestCacheIdentityIsolation:
    def test_anonymous_scope_is_anon(self):
        assert query_cache.identity_scope(None) == "anon"
        assert query_cache.identity_scope(None, row_level_enabled=True) == "anon"

    def test_authenticated_scope_none_when_row_level_off(self):
        assert query_cache.identity_scope({"role": "AGENT"}, False) is None

    def test_authenticated_scope_isolated_by_identity(self):
        a = query_cache.identity_scope(
            {"role": "AGENT", "department": "催收一部"}, True
        )
        b = query_cache.identity_scope(
            {"role": "COMPLIANCE_OFFICER", "department": "合规部"}, True
        )
        assert a is not None and b is not None and a != b
        assert a == query_cache.identity_scope(
            {"role": "AGENT", "department": "催收一部"}, True
        )

    def test_cache_key_differs_anonymous_vs_user(self):
        anon = query_cache.make_cache_key("q", "regulation", 5, scope="anon")
        user = query_cache.make_cache_key("q", "regulation", 5, scope="u:abc")
        assert anon != user

    def test_cache_key_isolated_by_anonymous_whitelist(self, monkeypatch):
        """白名单变化 → flags_hash 隔离，收紧后不命中旧匿名缓存。"""
        monkeypatch.setattr(settings, "KB_ANONYMOUS_CATEGORIES", "regulation")
        key_wide = query_cache.make_cache_key("q", "regulation", 5, scope="anon")
        monkeypatch.setattr(settings, "KB_ANONYMOUS_CATEGORIES", "")
        key_narrow = query_cache.make_cache_key("q", "regulation", 5, scope="anon")
        assert key_wide != key_narrow


class TestAnonymousRouteCacheLeak:
    @pytest.fixture
    def fake_redis(self, monkeypatch):
        fake = FakeRedis()
        monkeypatch.setattr(query_cache, "_redis", fake)
        return fake

    @pytest.mark.asyncio
    async def test_anonymous_misses_authenticated_cache(self, monkeypatch, fake_redis):
        """缓存隔离：登录用户缓存中的受限结果不会返回给匿名请求。"""
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", True)
        monkeypatch.setattr(settings, "KB_ROW_LEVEL_ENABLED", True)
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)

        jwt = {"role": "AGENT", "department": "催收一部"}
        user_key = query_cache.make_cache_key(
            req.query, req.category, req.top_k,
            scope=query_cache.identity_scope(jwt, True),
        )
        leaked = {
            "code": "0",
            "message": "success",
            "data": {"results": [{"doc_id": "leaked-script", "content": "受限话术"}]},
            "trace_id": "user",
        }
        fake_redis.store[user_key] = json.dumps(leaked, ensure_ascii=False)

        embed = MockEmbed()
        resp = await search_api.hybrid_search(req, **_route_args(embed))
        assert resp["data"]["results"][0]["doc_id"] != "leaked-script"
        assert embed.calls == 1  # 未命中登录用户缓存，重新检索

        anon_key = query_cache.make_cache_key(
            req.query, req.category, req.top_k, scope="anon"
        )
        assert anon_key != user_key
        assert anon_key in fake_redis.store

    @pytest.mark.asyncio
    async def test_anonymous_cache_not_served_to_authenticated(
        self, monkeypatch, fake_redis
    ):
        """缓存隔离：匿名缓存不会返回给行级权限开启的登录用户。"""
        monkeypatch.setattr(settings, "KB_CACHE_ENABLED", True)
        monkeypatch.setattr(settings, "KB_ROW_LEVEL_ENABLED", True)
        # 模拟携带有效身份（JWT 校验本身由其他用例覆盖）
        monkeypatch.setattr(
            search_api,
            "get_jwt_payload",
            lambda _request: {"role": "AGENT", "department": "催收一部"},
        )
        req = SearchRequest(query="逾期协商", category="regulation", top_k=5)

        anon_key = query_cache.make_cache_key(
            req.query, req.category, req.top_k, scope="anon"
        )
        fake_redis.store[anon_key] = json.dumps(
            {"code": "0", "data": {"results": [{"doc_id": "anon-only"}]}},
            ensure_ascii=False,
        )

        embed = MockEmbed()
        args = _route_args(embed)
        # T67：行级权限开启的登录场景必须注入行级服务（未注入将显式拒绝）
        args["row_access_svc"] = RowAccessService()
        resp = await search_api.hybrid_search(req, **args)
        assert embed.calls == 1
        assert resp["data"]["results"][0]["doc_id"] != "anon-only"

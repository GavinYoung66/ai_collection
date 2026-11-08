"""T92：rag documents list/get 行级权限对齐。

- 匿名列表仅含白名单类目（KB_ANONYMOUS_CATEGORIES 默认 regulation）
- 匿名"先列表取 id 再读 case/script 详情"两步均被拦（无权限放大）
- 匿名读 regulation 详情 → content 保持（白名单公开语义）+ ingest_error/storage_path 剥离（spec 6.3.2-3）
- 受限类目详情 → 404 B0004（与 /search 过滤语义一致的拒绝形态，不含 content/ingest_error/storage_path）
- 登录态矩阵放行类目 → 全字段原结构；B0001/B0004 既有语义回归
- DELETE 既有 require_roles("ADMIN","SUPERVISOR") 保持
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db, get_row_access_service
from app.main import app
from app.services.row_access_service import RowAccessService
from common.errors.codes import ErrorCode
from common.security.rbac import create_token


class FakeSession:
    async def commit(self):  # noqa: ANN201
        return None

    async def rollback(self):  # noqa: ANN201
        return None


def _doc(category: str) -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        title=f"doc-{category}",
        category=category,
        content=f"{category} 全文内容",
        status="ready",
        version=1,
        create_time=None,
        update_time=None,
        summary="摘要",
        ingest_stage="done",
        ingest_progress=100,
        ingest_error=None,
    )


def _doc_row(category: str) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "title": f"doc-{category}",
        "category": category,
        "status": "ready",
        "ingest_error": "boom",
    }


def _asset() -> SimpleNamespace:
    return SimpleNamespace(asset_type="image", page=1, storage_path="/data/staging/x.png", mime_type="image/png")


@pytest.fixture()
def client(monkeypatch):
    async def _allow(key, limit, window, context=None):  # noqa: ANN001, ARG001
        return True

    monkeypatch.setattr("app.services.rate_limit._rate_limit_allow", _allow)
    async def _session():
        yield FakeSession()

    app.dependency_overrides[get_db] = _session
    app.dependency_overrides[get_row_access_service] = lambda: RowAccessService()
    yield TestClient(app)
    app.dependency_overrides.clear()


# 现场事实：documents router 无 prefix，main.py:267 挂载时带 /api/v1/kb/documents
_DOC_LIST_PATH = "/api/v1/kb/documents"


def _doc_detail_path() -> str:
    return f"{_DOC_LIST_PATH}/{uuid.uuid4()}"


def _patch_repo(monkeypatch, *, rows: list[dict] | None = None, doc=None, total: int = 0):
    async def _list(self, *, category=None, status=None, page=1, size=20):  # noqa: ANN001, ARG001
        return (rows or [], total)

    async def _get_by_id(self, uid):  # noqa: ANN001, ARG001
        return doc

    async def _chunks(self, uid):  # noqa: ANN001, ARG001
        return 3

    async def _assets(self, uid):  # noqa: ANN001, ARG001
        return [_asset()]

    monkeypatch.setattr("app.api.documents.DocRepo.list_by_category", _list)
    monkeypatch.setattr("app.api.documents.DocRepo.get_by_id", _get_by_id)
    monkeypatch.setattr("app.api.documents.DocRepo.get_chunks_count", _chunks)
    monkeypatch.setattr("app.api.documents.AssetRepo.get_by_doc", _assets)


class TestListAccess:
    def test_anonymous_list_only_whitelisted_categories(self, client, monkeypatch):
        rows = [_doc_row("regulation"), _doc_row("case"), _doc_row("script"), _doc_row("regulation")]
        _patch_repo(monkeypatch, rows=rows, total=4)
        resp = client.get(_DOC_LIST_PATH)
        assert resp.status_code == 200
        body = resp.json()
        items = body["data"]["items"]
        categories = {item["category"] for item in items}
        assert categories == {"regulation"}
        # SUGGESTION-2：匿名列表条目剥离内部诊断字段 ingest_error（与详情同形态）
        assert all("ingest_error" not in item for item in items)

    def test_anonymous_category_filter_outside_whitelist_yields_empty(self, client, monkeypatch):
        _patch_repo(monkeypatch, rows=[_doc_row("case")], total=1)
        resp = client.get(_DOC_LIST_PATH, params={"category": "case"})
        assert resp.status_code == 200
        assert resp.json()["data"]["items"] == []

    def test_logged_in_matrix_allowed_categories_visible(self, client, monkeypatch):
        rows = [_doc_row("regulation"), _doc_row("case")]
        _patch_repo(monkeypatch, rows=rows, total=2)
        headers = {"Authorization": f"Bearer {create_token({'sub': 'u1', 'role': 'supervisor'})}"}
        resp = client.get(_DOC_LIST_PATH, headers=headers)
        items = resp.json()["data"]["items"]
        categories = {item["category"] for item in items}
        assert categories == {"regulation", "case"}
        # 登录态保留全字段（含 ingest_error 原结构）
        assert all("ingest_error" in item for item in items)


class TestDetailAccess:
    def test_anonymous_restricted_category_detail_404(self, client, monkeypatch):
        """匿名先列表拿 id 再读 case 详情 → 拒绝（两步均被拦，无权限放大）。

        B0004 的 HTTP 映射为 400（rag 既有语义，与 B0004 缺失回归同形态）。
        """
        _patch_repo(monkeypatch, doc=_doc("case"))
        resp = client.get(_doc_detail_path())
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "B0004"
        assert body.get("data") is None

    def test_anonymous_whitelisted_detail_keeps_content_strips_internal(self, client, monkeypatch):
        """匿名读 regulation 详情：content 保持（白名单公开语义）+ 内部诊断字段剥离。"""
        _patch_repo(monkeypatch, doc=_doc("regulation"))
        resp = client.get(_doc_detail_path())
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["content"] == "regulation 全文内容"
        assert "ingest_error" not in data
        assert data["assets"] and "storage_path" not in data["assets"][0]

    def test_logged_in_allowed_detail_full_structure(self, client, monkeypatch):
        _patch_repo(monkeypatch, doc=_doc("case"))
        headers = {"Authorization": f"Bearer {create_token({'sub': 'u1', 'role': 'supervisor'})}"}
        resp = client.get(_doc_detail_path(), headers=headers)
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["content"] == "case 全文内容"
        assert "ingest_error" in data
        assert data["assets"][0]["storage_path"] == "/data/staging/x.png"

    def test_b0001_invalid_doc_id_regression(self, client):
        resp = client.get(_DOC_LIST_PATH + "/not-a-uuid")
        assert resp.status_code in (200, 400, 422)
        body = resp.json()
        assert body.get("code") in ("B0001", "A2002") or resp.status_code == 422

    def test_b0004_missing_doc_regression(self, client, monkeypatch):
        """B0004 不存在语义保持（HTTP 映射 400 为 rag 既有形态）。"""
        _patch_repo(monkeypatch, doc=None)
        resp = client.get(_doc_detail_path())
        assert resp.status_code == 400
        assert resp.json()["code"] == "B0004"


class TestFailClosedDegradation:
    def test_matrix_load_failure_degrades_to_anonymous_whitelist(self, client, monkeypatch):
        """矩阵加载失败（T67 fail-closed）→ 登录态按匿名白名单处理。"""
        degraded = RowAccessService()
        degraded._degraded = True
        app.dependency_overrides[get_row_access_service] = lambda: degraded
        rows = [_doc_row("regulation"), _doc_row("case")]
        _patch_repo(monkeypatch, rows=rows, total=2)
        headers = {"Authorization": f"Bearer {create_token({'sub': 'u1', 'role': 'supervisor'})}"}
        resp = client.get(_DOC_LIST_PATH, headers=headers)
        categories = {item["category"] for item in resp.json()["data"]["items"]}
        assert categories == {"regulation"}


class TestDeleteAuthPreserved:
    def test_delete_endpoint_keeps_admin_supervisor_requirement(self):
        from app.api.documents import delete_document

        deps = [d.name for d in getattr(delete_document, "__dependencies__", [])]
        assert not deps  # FastAPI 依赖在路由层；此处校验源码级保持
        import inspect

        src = inspect.getsource(delete_document)
        assert 'require_roles("ADMIN", "SUPERVISOR")' in src
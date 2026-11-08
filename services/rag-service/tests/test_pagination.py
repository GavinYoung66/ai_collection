"""V1-T18 整改测试：列表分页 page/page_size 双支持（对齐 step5/02）。"""
from types import SimpleNamespace

import pytest

from app.api import documents as documents_api
from app.services.row_access_service import RowAccessService

# T92：list_documents 新增 request/row_access 参数——直接调用形态补齐
_FAKE_REQUEST = SimpleNamespace(headers={})


def _call_list(**kwargs):
    return documents_api.list_documents(_FAKE_REQUEST, row_access=RowAccessService(), **kwargs)


@pytest.fixture
def fake_list(monkeypatch):
    captured = {}

    async def fake_list_by_category(self, category=None, status=None, page=1, size=20):
        captured["category"] = category
        captured["status"] = status
        captured["page"] = page
        captured["size"] = size
        return [], 0

    monkeypatch.setattr(
        documents_api.DocRepo, "list_by_category", fake_list_by_category
    )
    return captured


class TestListPagination:
    @pytest.mark.asyncio
    async def test_default_page_size_20(self, fake_list):
        # 显式传 None 覆盖 Query 默认（绕过依赖注入时 Query() 为 FieldInfo 对象）
        resp = await _call_list(page=1, page_size=None, size=None)
        assert resp["code"] == "0"
        assert resp["data"]["page"] == 1
        assert resp["data"]["page_size"] == 20
        assert fake_list["size"] == 20

    @pytest.mark.asyncio
    async def test_page_size_preferred(self, fake_list):
        """page_size 与 size 同时传入时 page_size 优先。"""
        resp = await _call_list(page_size=30, size=50)
        assert resp["data"]["page_size"] == 30
        assert fake_list["size"] == 30

    @pytest.mark.asyncio
    async def test_size_compat_fallback(self, fake_list):
        """仅传 size（旧参数）时兼容回退。"""
        resp = await _call_list(page_size=None, size=50)
        assert resp["data"]["page_size"] == 50
        assert fake_list["size"] == 50

    @pytest.mark.asyncio
    async def test_page_param_passed(self, fake_list):
        resp = await _call_list(page=3, page_size=10)
        assert resp["data"]["page"] == 3
        assert fake_list["page"] == 3
        assert fake_list["size"] == 10

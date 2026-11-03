"""行级权限服务单元测试（§9.6 / §16.6）。

覆盖：
- 合规部 COMPLIANCE_OFFICER 只看 regulation
- AGENT 角色全放行 4 类
- 矩阵未知组合走 (default, default) 保底
- JWT 缺失（None）仅放行匿名白名单（T21）
"""
import pytest

from app.services.row_access_service import RowAccessService


@pytest.fixture
def svc():
    """加载项目默认 conf/row_access_matrix.yaml。"""
    return RowAccessService()


class TestRowAccessFilter:
    def test_compliance_officer_only_regulation(self, svc):
        """COMPLIANCE_OFFICER×合规部 → 仅 regulation 通过。"""
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation"},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script"},
            {"chunk_id": "c3", "doc_id": "d3", "category": "case"},
            {"chunk_id": "c4", "doc_id": "d4", "category": "other"},
        ]
        jwt = {"role": "COMPLIANCE_OFFICER", "department": "合规部"}
        result = svc.filter(items, jwt)
        assert len(result) == 1
        assert result[0]["category"] == "regulation"

    def test_agent_all_categories(self, svc):
        """AGENT×催收一部 → 全 4 类放行。"""
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation"},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script"},
            {"chunk_id": "c3", "doc_id": "d3", "category": "case"},
            {"chunk_id": "c4", "doc_id": "d4", "category": "other"},
        ]
        jwt = {"role": "AGENT", "department": "催收一部"}
        result = svc.filter(items, jwt)
        assert len(result) == 4

    def test_unknown_combo_falls_back_to_default(self, svc):
        """未知 (role, dept) 组合 → (default, default) 保底全放行。"""
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation"},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script"},
        ]
        jwt = {"role": "UNKNOWN_ROLE", "department": "未知部门"}
        result = svc.filter(items, jwt)
        assert len(result) == 2

    def test_jwt_none_only_anonymous_whitelist(self, svc, monkeypatch):
        """T21：JWT 缺失 → 仅放行 KB_ANONYMOUS_CATEGORIES（默认 regulation）。"""
        from app.settings import settings
        monkeypatch.setattr(settings, "KB_ANONYMOUS_CATEGORIES", "regulation")
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation"},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script"},
            {"chunk_id": "c3", "doc_id": "d3", "category": "case"},
            {"chunk_id": "c4", "doc_id": "d4", "category": "other"},
        ]
        result = svc.filter(items, jwt_payload=None)
        assert result == [items[0]]

    def test_department_unknown_uses_role_default(self, svc):
        """department='UNKNOWN' → 走 (role, default) 分支。"""
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation"},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script"},
        ]
        jwt = {"role": "COMPLIANCE_OFFICER", "department": "UNKNOWN"}
        result = svc.filter(items, jwt)
        # COMPLIANCE_OFFICER×default → 仅 regulation
        assert len(result) == 1
        assert result[0]["category"] == "regulation"

    def test_category_filter_intersection(self, svc):
        """category_filter 与 allowed 取交集。"""
        items = [
            {"chunk_id": "c1", "doc_id": "d1", "category": "regulation"},
            {"chunk_id": "c2", "doc_id": "d2", "category": "script"},
        ]
        jwt = {"role": "AGENT", "department": "催收一部"}
        result = svc.filter(items, jwt, category_filter="regulation")
        assert len(result) == 1
        assert result[0]["category"] == "regulation"

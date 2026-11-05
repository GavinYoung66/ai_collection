"""T67 / 7.1：行级权限矩阵失效 fail-closed 降级 单测。

验收判据：
- 删除矩阵文件后登录用户检索 script/case 类目被拒（降级为匿名白名单，权限不放大）；
- 空矩阵同样 fail-closed；
- 每次加载失败均记 error（不允许记一次后静默）；
- 匿名用户行为回归不变；正常矩阵恢复全量放行。

依赖：PYTHONPATH=../..:$PYTHONPATH
"""
from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.services import row_access_service as ras_mod
from app.services.row_access_service import RowAccessService

_ITEMS = [
    {"chunk_id": "c1", "category": "regulation"},
    {"chunk_id": "c2", "category": "script"},
    {"chunk_id": "c3", "category": "case"},
]


class TestRowAccessFailClosed:
    def test_missing_matrix_denies_script_for_logged_in(self, tmp_path):
        """矩阵文件缺失：登录用户（admin/ops）检索 script/case 被拒（不放大）。"""
        svc = RowAccessService(path=tmp_path / "missing.yaml")
        assert svc._degraded is True
        result = svc.filter(_ITEMS, {"role": "admin", "department": "ops"})
        cats = {item["category"] for item in result}
        assert "script" not in cats, "登录用户不得因矩阵失效获得受限类目"
        assert "case" not in cats
        assert cats <= {"regulation"}, "降级上限为匿名白名单"

    def test_empty_matrix_denies_script(self, tmp_path):
        """空矩阵文件：同样 fail-closed（error 而非 warning）。"""
        f = tmp_path / "empty.yaml"
        f.write_text("{}")
        spy = Mock()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ras_mod, "logger", spy)
            svc = RowAccessService(path=f)
        assert svc._degraded is True
        spy.error.assert_called_once()
        result = svc.filter(_ITEMS, {"role": "admin", "department": "ops"})
        assert all(item["category"] == "regulation" for item in result)

    def test_error_logged_on_every_failed_load(self, tmp_path):
        """每次加载失败均 error：连续两次失败 → error 两次（不留痕一次后静默）。"""
        spy = Mock()
        missing = tmp_path / "missing.yaml"
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(ras_mod, "logger", spy)
            RowAccessService(path=missing)
            RowAccessService(path=missing)
        assert spy.error.call_count == 2

    def test_anonymous_behavior_unchanged(self, tmp_path):
        """匿名行为回归不变：白名单本就是其上限。"""
        svc = RowAccessService(path=tmp_path / "missing.yaml")
        result = svc.filter(_ITEMS, None)
        assert {item["category"] for item in result} == {"regulation"}

    def test_valid_matrix_restores_matrix_access(self, tmp_path):
        """正常矩阵：登录用户按矩阵放行（降级标志清除，恢复 matrix 语义）。"""
        f = tmp_path / "ok.yaml"
        f.write_text('"(admin, ops)": ["regulation", "script", "case", "other"]')
        svc = RowAccessService(path=f)
        assert svc._degraded is False
        result = svc.filter(_ITEMS, {"role": "admin", "department": "ops"})
        assert {item["category"] for item in result} == {
            "regulation", "script", "case",
        }

    def test_search_service_rejects_explicitly_when_svc_missing(self):
        """KB_ROW_LEVEL_ENABLED=true 但 svc 未注入：显式 BizException（形态守护）。"""
        from pathlib import Path

        src = (
            Path(__file__).resolve().parents[1]
            / "app/services/search_service.py"
        ).read_text(encoding="utf-8")
        assert "行级服务未注入" in src
        assert "raise BizException(" in src, "必须显式拒绝而非静默降级"
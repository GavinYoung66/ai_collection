"""T117：rag current.dict 鉴权与限流。

- 匿名 GET /api/v1/sensitive-words/current.dict → 401 B0002，不返回全量防护词表
- 低权限角色 → 403 B0003；已授权正常返回字典纯文本
- 已授权高频拉取 → 429 限流（sensitive_dict 桶，10 次/60s 对齐 sensitive_refresh）
- 字典缺失 → 404 既有语义保持；refresh 端点行为回归（既有鉴权不变）
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
from common.security.rbac import create_token

_DICT_PATH = "/api/v1/sensitive-words/current.dict"
_REFRESH_PATH = "/api/v1/sensitive-words/refresh"

_DICT_TEXT = "上门催收\n影响征信\n冒充公检法\n"


@pytest.fixture()
def client_factory():
    def _make():
        return TestClient(app)

    return _make


def _patch_dict(monkeypatch, *, text: str | None = _DICT_TEXT, raise_exc: Exception | None = None):
    from app.api import sensitive_words as sw

    async def _get(self, *args, **kwargs):  # noqa: ANN001, ARG001
        if raise_exc is not None:
            raise raise_exc
        return text

    monkeypatch.setattr(
        "app.services.sensitive_words_sync.SensitiveWordsSyncService.get_current_dict", _get
    )
    return sw


def _patch_rate_limit(monkeypatch, *, limit: int = 10):
    """计数 mock：前 limit 次放行，其后拒绝（验证 429 形态与桶名）。"""
    import app.services.rate_limit as rl

    state = {"n": 0, "keys": []}

    async def _allow(key, limit_, window, context=None):  # noqa: ANN001, ARG001
        state["keys"].append(key)
        state["n"] += 1
        return state["n"] <= limit_

    monkeypatch.setattr(rl, "_rate_limit_allow", _allow)
    return state


def _admin_headers() -> dict:
    return {"Authorization": f"Bearer {create_token({'sub': 'u-admin', 'role': 'ADMIN'})}"}


class TestDictProtection:
    def test_anonymous_rejected_401_without_wordlist(self, client_factory, monkeypatch):
        _patch_dict(monkeypatch)
        client = client_factory()
        resp = client.get(_DICT_PATH)
        assert resp.status_code == 401
        assert resp.json()["code"] == "B0002"
        assert "上门催收" not in resp.text

    def test_low_privilege_role_403(self, client_factory, monkeypatch):
        _patch_dict(monkeypatch)
        client = client_factory()
        headers = {"Authorization": f"Bearer {create_token({'sub': 'u-agent', 'role': 'AGENT'})}"}
        resp = client.get(_DICT_PATH, headers=headers)
        assert resp.status_code == 403
        assert resp.json()["code"] == "B0003"

    def test_authorized_returns_plain_text(self, client_factory, monkeypatch):
        _patch_dict(monkeypatch)
        _patch_rate_limit(monkeypatch)
        client = client_factory()
        resp = client.get(_DICT_PATH, headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == _DICT_TEXT

    def test_high_frequency_authorized_429(self, client_factory, monkeypatch):
        _patch_dict(monkeypatch)
        state = _patch_rate_limit(monkeypatch, limit=10)
        client = client_factory()
        status_codes = []
        for _ in range(12):
            resp = client.get(_DICT_PATH, headers=_admin_headers())
            status_codes.append(resp.status_code)
        assert status_codes[:10] == [200] * 10
        assert status_codes[10] == 429 and status_codes[11] == 429
        assert "sensitive_dict" in state["keys"][0]

    def test_dict_missing_keeps_404_semantics(self, client_factory, monkeypatch):
        _patch_dict(monkeypatch, raise_exc=FileNotFoundError("敏感词字典文件不存在"))
        _patch_rate_limit(monkeypatch)
        client = client_factory()
        resp = client.get(_DICT_PATH, headers=_admin_headers())
        assert resp.status_code == 404

    def test_refresh_endpoint_keeps_existing_auth(self, client_factory, monkeypatch):
        client = client_factory()
        resp = client.post(_REFRESH_PATH)
        assert resp.status_code == 401


class TestExistingCasesAdapted:
    def test_dict_exists_returns_plain_text_with_admin(self, client_factory, monkeypatch, tmp_path):
        sw = _patch_dict(monkeypatch)
        _patch_rate_limit(monkeypatch)
        dict_file = tmp_path / "sensitive_words.dict"
        dict_file.write_text("上门催收\n影响征信\n", encoding="utf-8")
        monkeypatch.setattr(sw, "DICT_FILE", dict_file)

        async def _file_fallback(self, *args, **kwargs):  # noqa: ANN001, ARG001
            return dict_file.read_text(encoding="utf-8")

        monkeypatch.setattr(
            "app.services.sensitive_words_sync.SensitiveWordsSyncService.get_current_dict",
            _file_fallback,
        )
        client = client_factory()
        resp = client.get(_DICT_PATH, headers=_admin_headers())
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == "上门催收\n影响征信\n"

    def test_dict_missing_returns_404_with_admin(self, client_factory, monkeypatch, tmp_path):
        sw = _patch_dict(monkeypatch)
        _patch_rate_limit(monkeypatch)
        missing_file = tmp_path / "not_exist.dict"
        monkeypatch.setattr(sw, "DICT_FILE", missing_file)

        async def _not_found(self, *args, **kwargs):  # noqa: ANN001, ARG001
            raise FileNotFoundError("敏感词字典文件不存在")

        monkeypatch.setattr(
            "app.services.sensitive_words_sync.SensitiveWordsSyncService.get_current_dict",
            _not_found,
        )
        client = client_factory()
        resp = client.get(_DICT_PATH, headers=_admin_headers())
        assert resp.status_code == 404
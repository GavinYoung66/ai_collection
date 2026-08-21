"""敏感词只读接口测试：字典存在返回原文 / 字典缺失返回 404。"""
from fastapi.testclient import TestClient

from app.main import app
from app.api import sensitive_words as sw


class TestSensitiveWordsCurrentDict:
    def test_dict_exists_returns_plain_text(self, tmp_path, monkeypatch):
        """字典文件存在 → 200，Content-Type text/plain，正文与文件原文一致。"""
        dict_file = tmp_path / "sensitive_words.dict"
        dict_file.write_text("上门催收\n影响征信\n", encoding="utf-8")
        monkeypatch.setattr(sw, "DICT_FILE", dict_file)

        client = TestClient(app)
        resp = client.get("/api/v1/sensitive-words/current.dict")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == "上门催收\n影响征信\n"

    def test_dict_missing_returns_404(self, tmp_path, monkeypatch):
        """字典文件缺失 → 404，且不返回 500。"""
        missing_file = tmp_path / "not_exist.dict"
        monkeypatch.setattr(sw, "DICT_FILE", missing_file)

        client = TestClient(app)
        resp = client.get("/api/v1/sensitive-words/current.dict")

        assert resp.status_code == 404

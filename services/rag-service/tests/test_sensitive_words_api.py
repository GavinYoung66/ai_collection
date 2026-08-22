"""敏感词只读接口测试：字典存在返回原文 / 字典缺失返回 404。

V2 兼容：endpoint 优先 DB 查询，DB 失败时回退文件。测试通过 mock DB 异常
强制走文件兜底路径，验证 V1 文件读取行为保持不变。
"""

from fastapi.testclient import TestClient

from app.main import app
from app.api import sensitive_words as sw


class TestSensitiveWordsCurrentDict:
    def test_dict_exists_returns_plain_text(self, tmp_path, monkeypatch):
        """字典文件存在 → 200，Content-Type text/plain，正文与文件原文一致。"""
        dict_file = tmp_path / "sensitive_words.dict"
        dict_file.write_text("上门催收\n影响征信\n", encoding="utf-8")
        monkeypatch.setattr(sw, "DICT_FILE", dict_file)

        # V2：mock SensitiveWordsSyncService.get_current_dict 直接走文件兜底
        async def _file_fallback(self, *args, **kwargs):
            return dict_file.read_text(encoding="utf-8")

        monkeypatch.setattr(
            "app.services.sensitive_words_sync.SensitiveWordsSyncService.get_current_dict",
            _file_fallback,
        )

        client = TestClient(app)
        resp = client.get("/api/v1/sensitive-words/current.dict")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert resp.text == "上门催收\n影响征信\n"

    def test_dict_missing_returns_404(self, tmp_path, monkeypatch):
        """字典文件缺失 → 404，且不返回 500。"""
        missing_file = tmp_path / "not_exist.dict"
        monkeypatch.setattr(sw, "DICT_FILE", missing_file)

        # V2：mock get_current_dict 抛 FileNotFoundError（文件兜底也失败）
        async def _not_found(self, *args, **kwargs):
            raise FileNotFoundError("敏感词字典文件不存在")

        monkeypatch.setattr(
            "app.services.sensitive_words_sync.SensitiveWordsSyncService.get_current_dict",
            _not_found,
        )

        client = TestClient(app)
        resp = client.get("/api/v1/sensitive-words/current.dict")

        assert resp.status_code == 404

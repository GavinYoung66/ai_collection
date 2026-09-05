"""Gradio 上传防重与检索 query 不拼接测试（fix-rag-same-doc-recall）。"""
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_GRADIO_DIR = Path(__file__).resolve().parents[1] / "gradio"


def _load_gradio_formatters():
    spec = importlib.util.spec_from_file_location(
        "rag_gradio_formatters", _GRADIO_DIR / "formatters.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _load_rag_gradio_app():
    fmt_mod = _load_gradio_formatters()
    sys.modules["formatters"] = fmt_mod

    gr_mock = MagicMock()
    gr_mock.themes.Soft.return_value = MagicMock()
    sys.modules["gradio"] = gr_mock

    module_name = "rag_gradio_app_under_test"
    if module_name in sys.modules:
        return sys.modules[module_name]

    app_spec = importlib.util.spec_from_file_location(
        module_name, _GRADIO_DIR / "app.py"
    )
    app_mod = importlib.util.module_from_spec(app_spec)
    assert app_spec.loader is not None
    app_spec.loader.exec_module(app_mod)
    return app_mod


class TestQueryNotSpliced:
    def test_format_query_gate_shows_history_not_spliced(self):
        formatters = _load_gradio_formatters()
        md = formatters.format_query_gate_md(
            {"query_original": "技术类别有哪些"},
            user_query="技术类别有哪些",
            api_query="技术类别有哪些",
            spliced_n=2,
        )
        assert "技术类别有哪些" in md
        assert "未拼接进请求" in md


class TestTitleExistsPagination:
    @pytest.mark.asyncio
    async def test_processing_found_on_second_page(self):
        gradio_app = _load_rag_gradio_app()

        page1 = MagicMock(status_code=200)
        page1.json.return_value = {
            "data": {"items": [{"title": "other.docx"}], "total": 150},
        }
        page2 = MagicMock(status_code=200)
        page2.json.return_value = {
            "data": {"items": [{"title": "target.docx"}], "total": 150},
        }

        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=[page1, page2])

        with patch.object(gradio_app.httpx, "AsyncClient", return_value=mock_client):
            found = await gradio_app._title_exists_with_status("target.docx", "processing")
        assert found is True
        assert mock_client.get.await_count == 2

    @pytest.mark.asyncio
    async def test_active_title_detected(self):
        gradio_app = _load_rag_gradio_app()

        resp = MagicMock(status_code=200)
        resp.json.return_value = {
            "data": {"items": [{"title": "dup.docx"}], "total": 1},
        }
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=resp)

        with patch.object(gradio_app.httpx, "AsyncClient", return_value=mock_client):
            found = await gradio_app._find_active_title("dup.docx")
        assert found is True

"""检索响应扩展测试（V5，tasks 11.7）。

覆盖：results[].assets 填充/空数组两态（开关门控）/ 表格资产 storage_path 指向 content_json
预览文件（gr.Dataframe 渲染源指引）/ 响应 answer 字段缺省 null / 存量调用方不传 answer 行为不变 /
answer=true 且 LLM 可用时 answer 填充。
"""
import uuid
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest

from app.services.search_service import SearchService
from app.settings import settings

_MOCK_DOC_ID = str(uuid.uuid4())
_MOCK_CHUNK_ID = str(uuid.uuid4())


class FakeAsset:
    def __init__(self, asset_type, page, storage_path, mime_type):
        self.asset_type = asset_type
        self.page = page
        self.storage_path = storage_path
        self.mime_type = mime_type


class SpyAssetRepo:
    def __init__(self, assets=None):
        self.calls = 0
        self._assets = assets or []

    async def get_by_doc(self, doc_id):
        self.calls += 1
        return self._assets


class MockLLMService:
    def __init__(self, result=None):
        self._result = result or {
            "answer": None, "citations": [], "degraded": False, "llm_called": False,
        }
        self.generate_answer = AsyncMock(side_effect=self._generate)

    async def _generate(self, query, context, answer_requested):
        # 与真实 LLMService 门控一致：未传 answer=true 时零调用
        if not answer_requested:
            return {"answer": None, "citations": [], "degraded": False, "llm_called": False}
        return self._result


def _make_svc(asset_repo=None, llm_svc=None):
    class MockLogService:
        async def log(self, **kwargs):
            pass

    class MockDocRepo:
        def __init__(self):
            self.db = MagicMock()
            empty = MagicMock()
            empty.scalars.return_value.all.return_value = []
            self.db.execute = AsyncMock(return_value=empty)

        async def get_titles_map(self, uid_list):
            return {_MOCK_DOC_ID: "催收合规制度"}

        async def get_meta_dict(self, uid_list):
            return {_MOCK_DOC_ID: {"version": 2, "source": "v1_legacy"}}

    class MockMilvus:
        async def search(self, query_vector, top_n=20, category=None):
            return [{
                "chunk_id": _MOCK_CHUNK_ID,
                "doc_id": _MOCK_DOC_ID,
                "category": "regulation",
                "score": 0.9,
            }]

    class MockES:
        async def search(self, query, top_n=20, category=None):
            return [{
                "chunk_id": _MOCK_CHUNK_ID,
                "doc_id": _MOCK_DOC_ID,
                "category": "regulation",
                "content": "内容1",
                "score": 5.0,
            }]

    class MockEmbed:
        async def embed(self, text):
            return [0.1] * 1024

    class MockRerank:
        async def is_degraded(self):
            return False

        async def rerank(self, query, candidates):
            return [
                {"chunk_id": c["chunk_id"], "rerank_score": 0.9}
                for c in candidates
            ]

    return SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
        doc_repo=MockDocRepo(),
        asset_repo=asset_repo,
        llm_service=llm_svc,
    )


class TestSearchResponseAssets:
    @pytest.mark.asyncio
    async def test_assets_filled_when_switches_on(self, monkeypatch):
        """资产抽取开启：results[].assets 按 doc_id 关联填充（预览元数据）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", True)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        repo = SpyAssetRepo([
            FakeAsset("image", 1, f"{_MOCK_DOC_ID}/1_a_abc.png", "image/png"),
        ])
        svc = _make_svc(asset_repo=repo)
        result = await svc.hybrid_search("逾期协商", "regulation", 5)
        assert result["results"]
        assert repo.calls == 1
        item = result["results"][0]
        assert item["assets"]
        assert item["assets"][0]["asset_type"] == "image"
        assert item["assets"][0]["page"] == 1
        assert item["assets"][0]["storage_path"].endswith(".png")
        assert item["assets"][0]["mime_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_assets_empty_when_switches_off(self, monkeypatch):
        """资产抽取开关全关：不查 asset_repo，results[].assets 恒为空数组（V4 一致）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", False)
        repo = SpyAssetRepo()
        svc = _make_svc(asset_repo=repo)
        result = await svc.hybrid_search("逾期协商", "regulation", 5)
        assert result["results"]
        assert repo.calls == 0  # 开关关闭零 DB 开销
        assert all(item["assets"] == [] for item in result["results"])

    @pytest.mark.asyncio
    async def test_table_asset_storage_path_points_to_json(self, monkeypatch):
        """表格资产：storage_path 指向 content_json 预览文件（gr.Dataframe 渲染源指引）。"""
        monkeypatch.setattr(settings, "KB_IMAGE_EXTRACT_ENABLED", False)
        monkeypatch.setattr(settings, "KB_TABLE_EXTRACT_ENABLED", True)
        repo = SpyAssetRepo([
            FakeAsset("table", 2, f"{_MOCK_DOC_ID}/2_t_abc.json", "application/json"),
        ])
        svc = _make_svc(asset_repo=repo)
        result = await svc.hybrid_search("逾期协商", "regulation", 5)
        asset = result["results"][0]["assets"][0]
        assert asset["asset_type"] == "table"
        assert asset["page"] == 2
        assert asset["storage_path"].endswith(".json")
        assert asset["mime_type"] == "application/json"

    @pytest.mark.asyncio
    async def test_answer_field_defaults_null(self):
        """无 llm_service：响应 answer 缺省 null（V4 兼容，字段永远存在）。"""
        svc = _make_svc()
        result = await svc.hybrid_search("逾期协商", "regulation", 5)
        assert "answer" in result and result["answer"] is None
        assert result["degraded"] is False

    @pytest.mark.asyncio
    async def test_legacy_caller_without_answer_unchanged(self, monkeypatch):
        """存量调用方不传 answer：门控短路（answer_requested=False），answer 恒为 null。"""
        monkeypatch.setattr(settings, "KB_LLM_ANSWER_ENABLED", True)
        llm = MockLLMService(
            result={
                "answer": "不该被调用", "citations": [], "degraded": False, "llm_called": True,
            }
        )
        svc = _make_svc(llm_svc=llm)
        # 存量调用不传 answer 关键字（默认 False）
        result = await svc.hybrid_search("逾期协商", "regulation", 5)
        assert result["answer"] is None
        assert result["degraded"] is False
        # 门控在 LLMService 内部：以 answer_requested=False 调用即短路（零真实 LLM 调用）
        llm.generate_answer.assert_awaited_once_with(
            "逾期协商", ANY, answer_requested=False
        )

    @pytest.mark.asyncio
    async def test_answer_populated_when_requested(self, monkeypatch):
        """answer=true 且 LLM 可用：回答填充 + degraded=false + 日志 llm_called=1。"""
        monkeypatch.setattr(settings, "KB_LLM_ANSWER_ENABLED", True)

        class CapturingLog:
            def __init__(self):
                self.last = {}

            async def log(self, **kwargs):
                self.last = kwargs

        llm = MockLLMService(
            result={
                "answer": "可协商分期，最长 60 期",
                "citations": [_MOCK_DOC_ID],
                "degraded": False,
                "llm_called": True,
            }
        )
        log = CapturingLog()
        svc = SearchService(
            milvus_client=_FakeMilvus(),
            es_client=_FakeES(),
            embed_client=_FakeEmbed(),
            rerank_client=_FakeRerank(),
            log_service=log,
            doc_repo=_FakeDocRepo(),
            llm_service=llm,
        )
        result = await svc.hybrid_search("能否分期", "regulation", 5, answer=True)
        assert result["answer"] == "可协商分期，最长 60 期"
        assert result["citations"] == [_MOCK_DOC_ID]  # 溯源引用随响应返回
        assert result["degraded"] is False
        llm.generate_answer.assert_awaited_once()
        assert log.last.get("llm_called") == "1"
        assert log.last.get("llm_degraded") == "0"


class _FakeDocRepo:
    def __init__(self):
        self.db = MagicMock()
        empty = MagicMock()
        empty.scalars.return_value.all.return_value = []
        self.db.execute = AsyncMock(return_value=empty)

    async def get_titles_map(self, uid_list):
        return {_MOCK_DOC_ID: "催收合规制度"}

    async def get_meta_dict(self, uid_list):
        return {_MOCK_DOC_ID: {"version": 2, "source": "v1_legacy"}}


class _FakeMilvus:
    async def search(self, query_vector, top_n=20, category=None):
        return [{
            "chunk_id": _MOCK_CHUNK_ID,
            "doc_id": _MOCK_DOC_ID,
            "category": "regulation",
            "score": 0.9,
        }]


class _FakeES:
    async def search(self, query, top_n=20, category=None):
        return [{
            "chunk_id": _MOCK_CHUNK_ID,
            "doc_id": _MOCK_DOC_ID,
            "category": "regulation",
            "content": "内容1",
            "score": 5.0,
        }]


class _FakeEmbed:
    async def embed(self, text):
        return [0.1] * 1024


class _FakeRerank:
    async def is_degraded(self):
        return False

    async def rerank(self, query, candidates):
        return [
            {"chunk_id": c["chunk_id"], "rerank_score": 0.9}
            for c in candidates
        ]

"""rag-service 测试 fixtures。"""

import os

import pytest

from app.services.chunk_service import ChunkService
from app.services.search_service import SearchService


@pytest.fixture
def chunk_service():
    return ChunkService(chunk_size=100, chunk_overlap=20, min_length=10)


@pytest.fixture
def sample_text():
    return """催收合规制度第一条

催收人员在催收过程中必须遵守相关法律法规，不得使用威胁、恐吓等手段。

债务人逾期超过90天，可申请协商还款方案。协商方案应包括还款金额、还款期限及违约责任。

第二条关于通话录音。所有通话必须全程录音，录音保存期限不少于6个月。"""


@pytest.fixture
def sample_txt_file(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text(
        "催收合规制度\n\n第一条：催收人员不得使用威胁手段。\n\n"
        "第二条：逾期超过90天可申请协商还款。\n",
        encoding="utf-8",
    )
    return file_path


@pytest.fixture
def sample_md_file(tmp_path):
    file_path = tmp_path / "sample.md"
    file_path.write_text(
        "# 催收合规制度\n\n"
        "## 第一条\n"
        "催收人员**不得**使用威胁手段。\n\n"
        "## 第二条\n"
        "逾期超过90天可申请协商还款方案。\n",
        encoding="utf-8",
    )
    return file_path


@pytest.fixture
def mock_search_service():
    class MockLogService:
        async def log(self, **kwargs):
            pass

    class MockMilvus:
        async def search(self, query_vector, top_n=20, category=None):
            return [
                {"chunk_id": "chunk-1", "doc_id": "doc-1", "category": "regulation", "score": 0.9},
                {"chunk_id": "chunk-2", "doc_id": "doc-1", "category": "regulation", "score": 0.8},
            ]

    class MockES:
        async def search(self, query, top_n=20, category=None):
            return [
                {"chunk_id": "chunk-1", "doc_id": "doc-1", "category": "regulation", "content": "内容1", "score": 5.0},
                {"chunk_id": "chunk-3", "doc_id": "doc-2", "category": "regulation", "content": "内容3", "score": 3.0},
            ]

    class MockEmbed:
        async def embed(self, text):
            return [0.1] * 1024

    class MockRerank:
        async def is_degraded(self):
            return False

        async def rerank(self, query, candidates):
            return [
                {"chunk_id": c["chunk_id"], "rerank_score": float(0.9 - i * 0.3)}
                for i, c in enumerate(candidates)
            ]

    return SearchService(
        milvus_client=MockMilvus(),
        es_client=MockES(),
        embed_client=MockEmbed(),
        rerank_client=MockRerank(),
        log_service=MockLogService(),
    )


@pytest.fixture
async def async_db_session():
    """DB-backed AsyncSession fixture（受 `RAG_DB_TESTS` 环境变量控制，对齐 V2 skipif 约定）。

    - 未设置 `RAG_DB_TESTS=1` 时 `pytest.skip`（保持 seed 一致性用例默认跳过，回归不红）；
    - 设置后连接真实 PG 测试库，注入 AsyncSession 供用例使用，用完自动关闭。
    """
    if os.getenv("RAG_DB_TESTS") != "1":
        pytest.skip("RAG_DB_TESTS != 1，跳过需要真实 PG 的用例")

    from common.db.session import async_session_factory

    async with async_session_factory() as session:
        yield session

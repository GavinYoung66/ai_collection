"""文档管理 Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field

from app.settings import settings


class DocumentUploadResponse(BaseModel):
    doc_id: str
    chunks_count: int
    status: str


class DocumentListItem(BaseModel):
    id: str
    title: str
    category: str
    status: str
    chunks_count: int
    create_time: str


class DocumentListResponse(BaseModel):
    items: list[DocumentListItem]
    total: int
    page: int
    page_size: int  # 对齐 step5/02：page/page_size（默认 20，最大 100）


class DocumentDetailResponse(BaseModel):
    id: str
    title: str
    category: str
    content: str
    status: str
    version: int
    chunks_count: int
    create_time: str
    update_time: str
    # V4 新增：文档级摘要（上游预生成，PG 权威源；可空，默认 None，存量文档为 null）
    summary: str | None = Field(
        default=None,
        description=(
            "文档级摘要（str，可空）：由上游业务中台预生成，RAG-Service 仅存储/索引/检索返回；"
            "来源为 PG kb_documents.summary 权威源；存量/无摘要文档为 null"
        ),
    )


class SummaryUpdateRequest(BaseModel):
    """摘要补全请求体（V4 新增）。

    用于 `PUT /kb/documents/{doc_id}/summary`：对存量文档/多模态异步入库文档
    后补文档级摘要。摘要文本由上游业务中台预生成，RAG-Service 仅存储/索引/检索返回。
    """

    summary: str = Field(
        ...,
        max_length=settings.KB_SUMMARY_MAX_LEN,
        description="文档级摘要文本（上游预生成），长度上限 KB_SUMMARY_MAX_LEN（默认 200），超限返回 B0001",
    )

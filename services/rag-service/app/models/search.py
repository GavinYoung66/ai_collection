"""检索 Pydantic 请求/响应模型。"""
from pydantic import BaseModel, Field


class AssetPreview(BaseModel):
    """V5：命中 chunk 关联的内嵌资产预览元数据（Gradio 图片/表格预览渲染源）。

    - 由内嵌资产抽取（KB_IMAGE_EXTRACT_ENABLED / KB_TABLE_EXTRACT_ENABLED）填充 kb_doc_assets 后，
      检索命中时按 doc_id 关联返回；无关联资产时该字段为空数组；
    - storage_path 为相对路径（相对 KB_ASSETS_DIR），Gradio 从共享数据卷按此读取预览源；
      表格预览以 content_json（kb_doc_assets）为渲染单一数据源（本模型不含 content_json，
      预览由前端按 storage_path/doc_id 拉取）。
    """

    asset_type: str  # 'image' | 'table'
    page: int
    storage_path: str
    mime_type: str | None = None


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=500)
    category: str | None = Field(None, pattern="^(regulation|script|case|other)$")
    top_k: int = Field(5, ge=1, le=20)
    rewrite: bool = False  # 是否启用 LLM Query 改写（KB_QUERY_REWRITE_ENABLED）
    # V5: 是否生成 LLM 回答（仅当 KB_LLM_ANSWER_ENABLED=true 且检索未触发双闸门兜底时生效，
    # 其余情况零 LLM 调用；answer=true 请求跳过查询缓存读取且不写缓存，避免缓存响应缺 answer 语义悬空）
    answer: bool = Field(
        default=False,
        description=(
            "是否生成 LLM 回答（V5 可选，缺省 false）：仅当 KB_LLM_ANSWER_ENABLED=true "
            "且检索返回有效结果时调用 LLM；开启时跳过查询缓存读取与写入"
        ),
    )


class RewriteResult(BaseModel):
    """Query 改写结果。"""
    rewritten_query: str
    confidence: float
    degraded: bool  # 降级时为 True，rewritten_query 退回原始 query


class SourceItem(BaseModel):
    doc_id: str
    title: str
    chunk_index: int
    content: str
    score: float
    rerank_score: float
    # V2 溯源字段：存量 V1 文档统一缺省 version=1, source="v1_legacy"
    version: int = 1
    source: str | None = None
    # V4 新增：文档级摘要（永远存在，可空）。KB_SUMMARY_ENABLED=true 时填充
    # 该文档的文档级摘要（无摘要为 null）；开关关闭时恒为 null（检索语义与 V3 完全一致）。
    summary: str | None = Field(
        default=None,
        description=(
            "文档级摘要（str，可空，永远存在的字段）：KB_SUMMARY_ENABLED=true 时取 PG "
            "kb_documents.summary 权威源填充（无摘要为 null）；开关关闭时恒为 null（检索语义与 V3 完全一致）"
        ),
    )
    # V5 新增：关联内嵌资产预览元数据（缺省空数组，不改变既有字段）。
    # 资产抽取开关（KB_IMAGE_EXTRACT_ENABLED/KB_TABLE_EXTRACT_ENABLED）开启且命中 chunk
    # 关联存在资产时填充，否则空数组（开关关闭时不查 asset_repo，响应与 V4 完全一致）。
    assets: list[AssetPreview] = Field(
        default_factory=list,
        description=(
            "命中 chunk 关联的内嵌图片/表格预览元数据（V5 可选，缺省空数组）："
            "资产抽取开启且存在关联资产时填充 asset_type/page/storage_path/mime_type"
        ),
    )


class SearchResponse(BaseModel):
    results: list[SourceItem]
    degraded: bool
    query_original: str
    message: str | None = None
    trace_id: str
    # V5 新增：LLM 回答（可空，永远存在的字段）。KB_LLM_ANSWER_ENABLED=true 且请求 answer=true
    # 且检索未触发兜底时填充带溯源引用的回答文本；LLM 降级/超时/熔断时 answer=null 且 degraded=true；
    # 开关关闭或未请求时恒为 null（响应与 V4 完全一致）。
    answer: str | None = Field(
        default=None,
        description=(
            "LLM 回答文本（V5 可选，可空）：KB_LLM_ANSWER_ENABLED=true 且请求 answer=true "
            "且检索未触发双闸门兜底时填充；其余情况恒为 null"
        ),
    )
    # V5 新增：LLM 回答引用的命中文档 doc_id 列表（可空数组，永远存在的字段）。
    # 生成回答成功时由 LLMAnswer.citations 填充；未生成/降级时缺省空数组。
    # 用于回答可溯源审计（回答关键结论可回查命中文档），缺省不改变 V4 响应结构。
    citations: list[str] = Field(
        default_factory=list,
        description=(
            "LLM 回答引用的命中文档 doc_id 列表（V5 可选，缺省空数组）："
            "生成回答时填充，未生成/降级时为空数组"
        ),
    )

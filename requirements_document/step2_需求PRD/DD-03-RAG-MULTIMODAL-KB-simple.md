# 子系统详细设计 #03：RAG 知识库服务（简化版 V1）

> 子系统编号：第 3 号
> 服务名：`rag-service`
> 监听端口：**8002**
> 里程碑归属：**M1（最小闭环 RAG 流程跑通）**
> 上游约束：step1/01~03 全局口径、step3/01~03 架构与降级、step4/01~04 数据设计、step5/01~04 技术规范
> 复杂版参考：DD-03-RAG-MULTIMODAL-KB.md（V2.0，含多模态/复杂路由/Query改写/LangSmith，本期不实现）

---

## 0. 文档修订记录

| 版本 | 日期 | 修订人 | 修订内容 |
| :--- | :--- | :--- | :--- |
| V1.0 | 2026-08-20 | 规范优化 | **对齐项目实际技术栈重写**：PG+Milvus+ES+Redis 替代 MySQL+Qdrant+rank_bm25；补充完整目录结构、文件功能说明、业务流程、函数签名；明确 V1 范围与延后清单 |

---

## 1. 模块概览

### 1.1 定位

面向电话催收业务的**最简企业级 RAG 后端服务**。坐席输入业务问题，系统从私有合规文档中检索相关知识片段，返回带溯源引用的答案。

### 1.2 V1 核心目标（8 项，必须 100% 实现）

| # | 功能 | 说明 |
| :--- | :--- | :--- |
| 1 | 文档上传 + 解析入库 | 支持 PDF/DOCX/MD/TXT，自动解析→清洗→分块→向量化→双索引（Milvus+ES） |
| 2 | 增量更新/删除 | 单文档更新仅操作该文档的向量和索引，禁止全量重建 |
| 3 | 混合检索 | Milvus 向量语义检索 + ES BM25 关键词检索，RRF 融合 |
| 4 | Reranker 重排序 | 调用 rerank-service:8101 精排降噪 |
| 5 | 低分兜底（防幻觉） | Top1 rerank_score < 0.35 → 不送 LLM，返回兜底文案 |
| 6 | 溯源引用 | 回答附带来源文档名 + 原文片段，可核对 |
| 7 | 全链路日志 | 每次检索落库：query→召回片段→分数→LLM 输入输出→耗时 |
| 8 | 分类隔离 | 通过 kb_documents.category 实现知识分类过滤（regulation/script/case/other） |

### 1.3 V1 明确不做（延后到 V2+）

| 延后功能 | 延后原因 | V2+ 规划 |
| :--- | :--- | :--- |
| 多模态解析（音频 ASR/图片 OCR） | 需 PaddleOCR + ASR Provider，依赖重 | V2 |
| Query 预处理（改写+扩展+实体提取） | 需 LLM 预处理链，复杂度高 | V2 |
| 动态路由（规则类/复盘类权重调整） | 需意图分类器，V1 用固定 RRF 融合 | V2 |
| 业务加权（新通话×1.5, 高危×1.3） | 需元数据体系，V1 无 customer_id/agent_id | V2 |
| 行级权限隔离 | V1 用简单角色检查（ADMIN/AGENT） | V2 |
| 多轮对话 | V1 单轮一问一答 | V2 |
| 版本管理 + 灰度 | V1 用 active/archived 两状态 | V2 |
| LangSmith 全链路追踪 | V1 用 PG 日志表 | V2 |
| 评估模块（召回率/幻觉率） | V2 |
| Prompt 模板动态配置 | V1 硬编码默认 Prompt | V2 |
| 敏感词库 CRUD | 已在 M0 seed 初始化，V1 只读 | V2 |

### 1.4 AI 边界约束

- rag-service **只提供被动检索**，不主动向外调用服务
- 不修改案件状态（由 case-service）
- 不做话术生成本身（最终应答由 call-service LLM 生成，V1 返回检索片段 + 溯源）
- 不做质检打分（由 qc-service）

---

## 2. 技术栈（对齐项目 M0 基础设施）

| 组件 | 选型 | 说明 |
| :--- | :--- | :--- |
| Web 框架 | FastAPI + Uvicorn | 已有骨架 |
| 业务数据库 | PostgreSQL 15 | 已有 kb_documents / kb_chunks / sensitive_words 表 |
| 向量数据库 | Milvus 2.4.13 | 已运行，collection `kb_vectors`（BGE-M3 1024dim） |
| 关键词检索 | Elasticsearch 8.11.3 | 已运行，index `kb_bm25` |
| 缓存 | Redis 7 (DB 2) | 已运行，高频查询缓存 + 熔断标识 |
| Embedding | embedding-service:8100 | BGE-M3 1024dim，HTTP 调用 |
| Reranker | rerank-service:8101 | bge-reranker，HTTP 调用 |
| LLM | DashScope (qwen-plus) | 仅检索无匹配时不调用；V1 可选不接 LLM，只返回片段 |
| 配置 | pydantic-settings + .env | common/config/settings.py |
| 日志 | structlog | common/config/logging.py |
| 追踪 | X-Trace-Id | common/trace/middleware.py |
| 错误码 | A3001~A3006 | common/errors/codes.py |

---

## 3. 目录结构与文件清单

```
services/rag-service/
├── app/
│   ├── main.py                        # FastAPI 入口，注册路由 + 中间件 + /health
│   ├── api/                           # 路由层（接收 HTTP 请求，调用 service，返回统一响应包）
│   │   ├── __init__.py
│   │   ├── health.py                  # GET /health — 已有，健康检查
│   │   ├── documents.py               # 文档管理 API（上传/列表/详情/删除/重建索引）
│   │   └── search.py                  # 检索 API（混合检索 + 溯源 + 日志）
│   ├── services/                      # 业务逻辑层（核心业务编排）
│   │   ├── __init__.py
│   │   ├── ingest_service.py          # 文档入库编排：解析→清洗→分块→向量化→双索引写入
│   │   ├── chunk_service.py           # 文本分块：按语义边界切分 + 脏数据过滤
│   │   ├── search_service.py          # 混合检索编排：向量召回 + BM25 召回 → RRF 融合 → rerank → 兜底判断
│   │   └── log_service.py            # 全链路日志：查询→召回→分数→耗时→落库
│   ├── parsers/                       # 文档解析器（提取纯文本）
│   │   ├── __init__.py
│   │   ├── base.py                    # DocumentParser ABC：定义 extract_text() 接口
│   │   ├── pdf_parser.py              # PDF 解析：pdfplumber 提取文本，过滤页眉页脚
│   │   ├── docx_parser.py             # DOCX 解析：python-docx 提取段落文本
│   │   ├── md_parser.py               # Markdown 解析：正则去除标记符号
│   │   └── text_parser.py             # TXT 解析：UTF-8 读取 + 编码检测
│   ├── vectorstore/                   # 向量存储客户端
│   │   ├── __init__.py
│   │   ├── milvus_client.py           # Milvus 操作：create_collection / insert / search / delete_by_doc
│   │   ├── es_client.py               # ES 操作：create_index / index_doc / search / delete_by_doc
│   │   └── embed_client.py            # Embedding 客户端：HTTP 调用 embedding-service:8100/embed
│   ├── repositories/                  # 数据访问层（PG CRUD）
│   │   ├── __init__.py
│   │   ├── doc_repo.py                # kb_documents CRUD + 状态流转
│   │   └── chunk_repo.py              # kb_chunks CRUD + 按 doc_id 批量删除
│   ├── models/                        # Pydantic 请求/响应模型
│   │   ├── __init__.py
│   │   ├── document.py                # DocumentUpload / DocumentResponse / DocumentListResponse
│   │   └── search.py                  # SearchRequest / SearchResult / SearchResponse / SourceItem
│   └── tests/                         # 测试
│       ├── __init__.py
│       ├── test_health.py             # 已有
│       ├── test_documents.py          # 文档上传/列表/删除测试
│       └── test_search.py             # 混合检索测试
├── Dockerfile                         # 已有
```

### 3.1 文件功能说明

#### app/main.py（已有，需扩展）

```python
"""RAG Service FastAPI 入口。"""
from fastapi import FastAPI

from common.config.logging import setup_logging
from common.errors.handlers import register_exception_handlers
from common.trace.middleware import TraceMiddleware
from app.api.health import router as health_router
from app.api.documents import router as documents_router
from app.api.search import router as search_router

setup_logging()
app = FastAPI(title="RAG Service", version="1.0.0")
app.add_middleware(TraceMiddleware)
register_exception_handlers(app)

app.include_router(health_router, prefix="/api/v1")
app.include_router(documents_router, prefix="/api/v1/kb/documents")
app.include_router(search_router, prefix="/api/v1/kb")

@app.on_event("startup")
async def startup():
    """启动时初始化 Milvus collection + ES index。"""
    from app.vectorstore.milvus_client import MilvusClient
    from app.vectorstore.es_client import ESClient
    milvus = MilvusClient()
    await milvus.ensure_collection()
    es = ESClient()
    await es.ensure_index()
```

#### app/api/documents.py — 文档管理路由

```python
"""文档管理 API。"""
from fastapi import APIRouter, UploadFile, File, Form, Depends

router = APIRouter(tags=["文档管理"])

@router.post("")
async def upload_document(
    file: UploadFile = File(...),
    category: str = Form(..., description="regulation/script/case/other"),
):
    """上传文档 → 解析 → 分块 → 向量化 → 双索引写入。
    返回 {doc_id, chunks_count, status}"""
    ...

@router.get("")
async def list_documents(category: str = None, page: int = 1, size: int = 20):
    """文档列表分页。"""
    ...

@router.get("/{doc_id}")
async def get_document(doc_id: str):
    """文档详情（元数据 + chunks 数量）。"""
    ...

@router.delete("/{doc_id}")
async def delete_document(doc_id: str):
    """增量删除：删 PG chunks + Milvus vectors + ES docs，不重建全库。"""
    ...

@router.post("/{doc_id}/reindex")
async def reindex_document(doc_id: str):
    """重新解析入库（删旧索引 → 重新分块向量化）。"""
    ...
```

#### app/api/search.py — 检索路由

```python
"""混合检索 API。"""
from fastapi import APIRouter
from app.models.search import SearchRequest, SearchResponse

router = APIRouter(tags=["混合检索"])

@router.post("/search", response_model=SearchResponse)
async def hybrid_search(req: SearchRequest):
    """混合检索：向量 + BM25 → RRF 融合 → Rerank → 兜底判断 → 溯源输出。
    
    请求体：
        query: str          — 用户问题
        category: str       — 知识分类过滤（可选）
        top_k: int = 5       — 返回条数
        include_answer: bool = False — 是否调用 LLM 生成答案（V1 默认 False，只返回片段）
    
    响应体：
        results: [{content, source, score, rerank_score, doc_id, chunk_index}]
        degraded: bool       — 是否降级
        query_original: str
        trace_id: str
    """
    ...
```

#### app/services/ingest_service.py — 文档入库编排

```python
"""文档入库编排：解析 → 清洗 → 分块 → 向量化 → 双索引写入。"""
from pathlib import Path

class IngestService:
    def __init__(self, doc_repo, chunk_repo, milvus_client, es_client, embed_client):
        self.doc_repo = doc_repo
        self.chunk_repo = chunk_repo
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client

    async def ingest(self, file_path: Path, filename: str, category: str) -> dict:
        """完整入库流程。
        
        步骤：
        1. 根据 extension 选择 Parser
        2. parser.extract_text(file_path) → raw_text
        3. 清洗：去空行、多余空格、页眉页脚
        4. INSERT kb_documents(status='processing')
        5. chunk_service.split(text, category) → chunks[]
        6. embed_client.batch_embed(chunks) → vectors[]
        7. milvus_client.insert(vectors, metadata) + es_client.index(chunks, metadata)
        8. INSERT kb_chunks (vector_id, content, category, doc_id, chunk_index)
        9. UPDATE kb_documents(status='active')
        10. 返回 {doc_id, chunks_count, status}
        
        异常处理：
        - 解析失败 → A3001, status='failed'
        - 切片失败 → A3002, status='failed'
        - 向量化失败 → A3003, status='failed'
        """
        ...

    async def delete_by_doc(self, doc_id: str) -> None:
        """增量删除：仅删该文档的 chunks + vectors + ES docs。"""
        # 1. chunk_repo.delete_by_doc(doc_id)
        # 2. milvus_client.delete_by_doc(doc_id)
        # 3. es_client.delete_by_doc(doc_id)
        # 4. doc_repo.update_status(doc_id, 'archived')
        ...
```

#### app/services/chunk_service.py — 文本分块

```python
"""文本分块：按语义边界切分 + 脏数据过滤。"""

class ChunkService:
    def __init__(self, chunk_size: int = 500, chunk_overlap: int = 50, min_length: int = 10):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_length = min_length

    def split(self, text: str, category: str) -> list[dict]:
        """按语义边界分块。
        
        策略：
        1. 按段落（\\n\\n）切分
        2. 段落超过 chunk_size → 按句号切分
        3. 句子超过 chunk_size → 按 chunk_size 硬切，保留 chunk_overlap 重叠
        4. 过滤 len(chunk) < min_length 的片段
        5. 返回 [{content, chunk_index, category}]
        """
        ...

    def clean(self, text: str) -> str:
        """清洗：去页眉页脚、多余空行、连续空格。"""
        ...
```

#### app/services/search_service.py — 混合检索编排

```python
"""混合检索编排：向量召回 + BM25 召回 → RRF 融合 → Rerank → 兜底。"""
import asyncio

class SearchService:
    def __init__(self, milvus_client, es_client, embed_client, rerank_client, log_service):
        self.milvus = milvus_client
        self.es = es_client
        self.embed = embed_client
        self.rerank = rerank_client
        self.logger = log_service

    async def hybrid_search(self, query: str, category: str = None,
                            top_k: int = 5) -> dict:
        """混合检索全流程。
        
        步骤：
        1. embed_client.embed(query) → 1024dim query_vector
        2. 并行召回：
           - milvus.search(query_vector, top_n=20, filter=category) → dense_hits
           - es.search(query, top_n=20, filter=category) → sparse_hits
        3. RRF 融合：score = 1/(60+rank_dense) + 1/(60+rank_sparse)，去重
        4. rerank_client.rerank(query, candidates) → reranked (带 rerank_score)
        5. 兜底判断：if reranked[0].rerank_score < 0.35 → 返回空 + 兜底文案
        6. 截取 top_k
        7. log_service.log(query, hits, reranked, latency) → PG
        8. 返回 {results, degraded, query_original, trace_id}
        
        降级：
        - Milvus 宕机 → ES-only（degraded=True）
        - ES 宕机 → Milvus-only（degraded=True）
        - 双侧宕机 → 返回 A3005
        - Rerank 宕机 → 用 RRF score 排序（degraded=True）
        """
        ...

    def _rrf_fuse(self, dense_hits: list, sparse_hits: list, k: int = 60) -> list:
        """RRF 倒数排名融合。
        
        score = 1/(k + rank_dense) + 1/(k + rank_sparse)
        按 chunk_id 去重，保留高分。
        """
        ...

    async def _fallback(self, query: str) -> dict:
        """兜底：返回固定文案，不送 LLM。"""
        return {
            "results": [],
            "degraded": False,
            "message": "知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。",
        }
```

#### app/services/log_service.py — 全链路日志

```python
"""全链路检索日志：query→召回→分数→耗时→落库。"""
import time

class LogService:
    def __init__(self, db_session):
        self.db = db_session

    async def log(self, query: str, dense_hits: list, sparse_hits: list,
                  reranked: list, latency_ms: int, degraded: bool,
                  trace_id: str) -> None:
        """记录检索日志到 kb_search_logs 表。
        
        字段：
        - query, trace_id
        - dense_hit_count, sparse_hit_count
        - fused_count, reranked_count
        - max_rerank_score
        - latency_ms, degraded
        - hit_doc_ids, hit_chunk_ids
        - created_at
        """
        ...
```

#### app/parsers/base.py — 解析器基类

```python
"""文档解析器抽象基类。"""
from abc import ABC, abstractmethod
from pathlib import Path

class DocumentParser(ABC):
    @abstractmethod
    async def extract_text(self, file_path: Path) -> str:
        """提取纯文本，返回清洗后的文本。"""
        ...
```

#### app/parsers/pdf_parser.py — PDF 解析

```python
"""PDF 解析器：pdfplumber 提取文本。"""
import pdfplumber

class PDFParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        """提取文本，过滤页眉页脚。
        
        策略：
        1. pdfplumber.open(file_path)
        2. 逐页提取 text
        3. 去除页眉页脚（首尾各 2 行）
        4. 拼接全文本
        """
        ...
```

#### app/parsers/docx_parser.py — DOCX 解析

```python
"""DOCX 解析器：python-docx 提取段落文本。"""
from docx import Document

class DocxParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        """提取段落文本，保留标题层级。"""
        ...
```

#### app/parsers/text_parser.py — TXT 解析

```python
"""TXT 解析器：UTF-8 读取 + 编码检测。"""

class TextParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        """读取文本，自动检测编码。"""
        ...
```

#### app/parsers/md_parser.py — Markdown 解析

```python
"""Markdown 解析器：去除标记符号。"""
import re

class MDParser(DocumentParser):
    async def extract_text(self, file_path: Path) -> str:
        """读取 MD 文件，去除 #/*/- 等标记，保留纯文本。"""
        ...
```

#### app/vectorstore/milvus_client.py — Milvus 操作

```python
"""Milvus 客户端：collection 管理 + 向量增删查。"""
from pymilvus import connections, Collection, FieldSchema, CollectionSchema, DataType

class MilvusClient:
    COLLECTION_NAME = "kb_vectors"
    DIM = 1024  # BGE-M3

    async def ensure_collection(self) -> None:
        """启动时确保 collection 存在。
        
        Schema：
        - chunk_id: INT64, PK (对应 kb_chunks.id 的数字)
        - vector: FLOAT_VECTOR(1024)
        - doc_id: INT64
        - category: VARCHAR(50)
        
        索引：IVF_FLAT, nlist=1024, metric=IP
        """
        ...

    async def insert(self, vectors: list, metadata: list[dict]) -> list[str]:
        """批量插入向量，返回 vector_id 列表。"""
        ...

    async def search(self, query_vector: list, top_n: int = 20,
                     category: str = None) -> list[dict]:
        """向量检索，返回 [{chunk_id, doc_id, score, category}]。"""
        ...

    async def delete_by_doc(self, doc_id: str) -> None:
        """按 doc_id 删除该文档的所有向量。"""
        ...
```

#### app/vectorstore/es_client.py — ES 操作

```python
"""Elasticsearch 客户端：BM25 索引管理 + 文档增删查。"""
from elasticsearch import AsyncElasticsearch

class ESClient:
    INDEX_NAME = "kb_bm25"

    async def ensure_index(self) -> None:
        """启动时确保 index 存在。
        
        Mapping：
        - chunk_id: keyword
        - content: text (analyzer=ik_max_word)
        - doc_id: keyword
        - category: keyword
        """
        ...

    async def index_docs(self, chunks: list[dict]) -> None:
        """批量索引文档到 ES。"""
        ...

    async def search(self, query: str, top_n: int = 20,
                     category: str = None) -> list[dict]:
        """BM25 检索，返回 [{chunk_id, doc_id, score, content}]。"""
        ...

    async def delete_by_doc(self, doc_id: str) -> None:
        """按 doc_id 删除该文档的所有 ES 文档。"""
        ...
```

#### app/vectorstore/embed_client.py — Embedding 客户端

```python
"""Embedding 客户端：HTTP 调用 embedding-service:8100。"""
import httpx

class EmbedClient:
    def __init__(self, endpoint: str = "http://localhost:8100"):
        self.endpoint = endpoint

    async def embed(self, text: str) -> list[float]:
        """单条文本 → 1024dim 向量。
        
        POST /embed {"text": "..."} → {"vector": [...]}
        超时 5s，重试 3 次，熔断 3 连失败。
        """
        ...

    async def batch_embed(self, texts: list[str]) -> list[list[float]]:
        """批量向量化。"""
        ...
```

#### app/repositories/doc_repo.py — 文档数据访问

```python
"""kb_documents 数据访问层。"""
from sqlalchemy.ext.asyncio import AsyncSession

class DocRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, title: str, category: str, content: str,
                     created_by: str) -> str:
        """INSERT kb_documents(status='processing')，返回 doc_id。"""
        ...

    async def get_by_id(self, doc_id: str) -> dict | None:
        """查文档详情。"""
        ...

    async def list_by_category(self, category: str = None,
                               page: int = 1, size: int = 20) -> tuple[list, int]:
        """分页列表，返回 (items, total)。"""
        ...

    async def update_status(self, doc_id: str, status: str) -> None:
        """更新文档状态：processing / active / archived / failed。"""
        ...

    async def soft_delete(self, doc_id: str) -> None:
        """软删除：is_delete='1'。"""
        ...
```

#### app/repositories/chunk_repo.py — 分块数据访问

```python
"""kb_chunks 数据访问层。"""

class ChunkRepo:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def batch_create(self, chunks: list[dict]) -> None:
        """批量 INSERT kb_chunks。"""
        ...

    async def delete_by_doc(self, doc_id: str) -> int:
        """按 doc_id 删除所有 chunks，返回删除数量。"""
        ...

    async def get_by_ids(self, chunk_ids: list[str]) -> list[dict]:
        """按 ID 列表查 chunks（用于溯源）。"""
        ...
```

#### app/models/document.py — 文档模型

```python
"""文档请求/响应 Pydantic 模型。"""
from pydantic import BaseModel

class DocumentUploadResponse(BaseModel):
    doc_id: str
    chunks_count: int
    status: str  # processing / active / failed

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
    size: int
```

#### app/models/search.py — 检索模型

```python
"""检索请求/响应 Pydantic 模型。"""
from pydantic import BaseModel

class SearchRequest(BaseModel):
    query: str
    category: str | None = None  # regulation/script/case/other
    top_k: int = 5
    include_answer: bool = False  # V1 默认 False，只返回片段

class SourceItem(BaseModel):
    doc_id: str
    title: str
    chunk_index: int
    content: str  # 原文片段
    score: float  # RRF 融合分数
    rerank_score: float  # rerank 分数

class SearchResponse(BaseModel):
    results: list[SourceItem]
    degraded: bool
    query_original: str
    message: str | None = None  # 兜底文案
    trace_id: str
```

---

## 4. 数据库表与存储

### 4.1 PostgreSQL（已有表，对齐 M0 迁移）

#### kb_documents（已有，无需改动）

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | UUID PK | 默认 uuid_generate_v4() |
| title | VARCHAR(255) | 文档标题 |
| category | VARCHAR(50) | regulation / script / case / other |
| content | TEXT | 原文全文 |
| version | INT | 默认 1（V1 不做版本管理，始终 1） |
| status | VARCHAR(20) | processing / active / archived / failed |
| created_by | UUID FK→users | 上传者 |
| create_time / update_time | TIMESTAMP | 公共字段 |
| is_delete | CHAR(1) | 软删除 |

> **注意**：M0 迁移中 status CHECK 约束为 `('active','archived')`，需通过 Alembic 迁移扩展为 `('processing','active','archived','failed')`。

#### kb_chunks（已有，需新增 vector_id 格式规范）

| 列 | 类型 | 说明 |
| :--- | :--- | :--- |
| id | UUID PK | chunk UUID |
| doc_id | UUID FK→kb_documents | 所属文档 |
| chunk_index | INT | 分块序号 |
| content | TEXT | 分块文本 |
| vector_id | VARCHAR(100) | Milvus 中的向量 ID |
| category | VARCHAR(50) | 冗余，用于检索过滤 |
| version | INT | 默认 1 |
| create_time / update_time | TIMESTAMP | 公共字段 |
| is_delete | CHAR(1) | 软删除 |

#### kb_search_logs（新增 — 全链路日志表）

```sql
CREATE TABLE kb_search_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    trace_id VARCHAR(64) NOT NULL,
    query TEXT NOT NULL,
    category VARCHAR(50),
    dense_hit_count INT DEFAULT 0,
    sparse_hit_count INT DEFAULT 0,
    fused_count INT DEFAULT 0,
    reranked_count INT DEFAULT 0,
    max_rerank_score FLOAT,
    hit_doc_ids TEXT,        -- 逗号分隔的 doc_id 列表
    hit_chunk_ids TEXT,     -- 逗号分隔的 chunk_id 列表
    latency_ms INT,
    degraded BOOLEAN DEFAULT FALSE,
    is_fallback BOOLEAN DEFAULT FALSE,  -- 是否触发了兜底
    create_time TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    is_delete CHAR(1) NOT NULL DEFAULT '0' CHECK (is_delete IN ('0','1'))
);
CREATE INDEX idx_kb_search_logs_trace ON kb_search_logs(trace_id);
CREATE INDEX idx_kb_search_logs_created ON kb_search_logs(create_time);
```

### 4.2 Milvus Collection `kb_vectors`（需创建）

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| chunk_id | INT64, PK | kb_chunks.id 的数字表示（UUID → hash → INT64） |
| vector | FLOAT_VECTOR(1024) | BGE-M3 输出 |
| doc_id | INT64 | kb_documents.id 的数字表示 |
| category | VARCHAR(50) | regulation/script/case/other |

> 索引参数：IVF_FLAT, nlist=1024, metric_type=IP（内积）
> **注意**：UUID 无法直接作为 Milvus PK，需转换为 INT64（取 UUID 前 8 字节的整数表示，或使用自增序列）

### 4.3 Elasticsearch Index `kb_bm25`（需创建）

| 字段 | 类型 | 说明 |
| :--- | :--- | :--- |
| chunk_id | keyword | kb_chunks.id（UUID 字符串） |
| content | text | BM25 检索字段，analyzer=ik_max_word |
| doc_id | keyword | kb_documents.id |
| category | keyword | 分类过滤 |

### 4.4 Redis（DB 2，rag-service 专用）

```
rag:cache:query:{hash}    — 高频查询缓存（TTL=1h）
rag:embed:degraded         — Embedding 熔断标识
rag:rerank:degraded        — Rerank 熔断标识
```

---

## 5. 对外接口

所有接口 `http://rag-service:8002/api/v1`，统一响应包 `{"code":"0","message":str,"data":any|null,"trace_id":str}`。

### 5.1 文档管理

| 方法 | 路径 | 职责 | 权限 | 限流 |
| :--- | :--- | :--- | :--- | :--- |
| POST | `/kb/documents` | 上传文档（multipart/form-data） | ADMIN/SUPERVISOR | 20/min |
| GET | `/kb/documents` | 列表分页（category/status 过滤） | ALL | 60/min |
| GET | `/kb/documents/{doc_id}` | 详情（元数据 + chunks 数量） | ALL | 60/min |
| DELETE | `/kb/documents/{doc_id}` | 增量删除（不重建全库） | ADMIN/SUPERVISOR | 20/min |
| POST | `/kb/documents/{doc_id}/reindex` | 重新解析入库 | ADMIN/SUPERVISOR | 10/min |

**上传请求**：`multipart/form-data`
- `file`: 文件二进制
- `category`: `regulation` / `script` / `case` / `other`

**上传响应**：
```json
{
  "code": "0",
  "data": {
    "doc_id": "uuid",
    "chunks_count": 15,
    "status": "active"
  },
  "trace_id": "xxx"
}
```

### 5.2 混合检索

| 方法 | 路径 | 职责 | 权限 | 限流 |
| :--- | :--- | :--- | :--- | :--- |
| POST | `/kb/search` | 混合检索（向量+BM25→RRF→Rerank→溯源） | ALL | 200/min |

**请求体**：
```json
{
  "query": "逾期多久可以协商还款",
  "category": "regulation",
  "top_k": 5,
  "include_answer": false
}
```

**响应体**：
```json
{
  "code": "0",
  "data": {
    "results": [
      {
        "doc_id": "uuid",
        "title": "催收自律公约.pdf",
        "chunk_index": 3,
        "content": "债务人逾期超过90天，可申请协商还款方案...",
        "score": 0.032,
        "rerank_score": 0.87
      }
    ],
    "degraded": false,
    "query_original": "逾期多久可以协商还款",
    "message": null,
    "trace_id": "xxx"
  },
  "trace_id": "xxx"
}
```

**兜底响应（score < 0.35）**：
```json
{
  "code": "0",
  "data": {
    "results": [],
    "degraded": false,
    "query_original": "逾期多久可以协商还款",
    "message": "知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。",
    "trace_id": "xxx"
  },
  "trace_id": "xxx"
}
```

### 5.3 敏感词（只读，V1）

| 方法 | 路径 | 职责 | 权限 |
| :--- | :--- | :--- | :--- |
| GET | `/sensitive-words/current.dict` | 内部接口，返回纯数组 | INTERNAL |

---

## 6. 业务流程

### 6.1 文档入库流程

```
管理员上传文件 (PDF/DOCX/MD/TXT) + category
    │
    ▼
IngestService.ingest(file_path, filename, category)
    │
    ├── 1. 选择 Parser（按文件扩展名）
    │      .pdf → PDFParser / .docx → DocxParser / .md → MDParser / .txt → TextParser
    │
    ├── 2. Parser.extract_text(file_path) → raw_text
    │
    ├── 3. ChunkService.clean(raw_text) → cleaned_text
    │      去空行、多余空格、页眉页脚
    │
    ├── 4. DocRepo.create(title, category, content, created_by)
    │      INSERT kb_documents(status='processing') → doc_id
    │
    ├── 5. ChunkService.split(cleaned_text, category) → chunks[{content, chunk_index}]
    │      按段落→句号→chunk_size 切分，过滤 len<10
    │      失败 → A3002, status='failed'
    │
    ├── 6. EmbedClient.batch_embed(chunks) → vectors[1024dim]
    │      失败 → A3003, status='failed'
    │
    ├── 7. MilvusClient.insert(vectors, metadata) → vector_ids
    │      ESClient.index_docs(chunks + metadata)
    │
    ├── 8. ChunkRepo.batch_create(doc_id, chunks, vector_ids)
    │      INSERT kb_chunks
    │
    ├── 9. DocRepo.update_status(doc_id, 'active')
    │
    └── 10. 返回 {doc_id, chunks_count, status='active'}
```

### 6.2 增量删除流程

```
管理员删除文档 doc_id
    │
    ▼
IngestService.delete_by_doc(doc_id)
    │
    ├── 1. ChunkRepo.delete_by_doc(doc_id) → 删 PG chunks
    ├── 2. MilvusClient.delete_by_doc(doc_id) → 删 Milvus vectors
    ├── 3. ESClient.delete_by_doc(doc_id) → 删 ES docs
    └── 4. DocRepo.update_status(doc_id, 'archived')
```

### 6.3 混合检索流程

```
用户 Query + category + top_k
    │
    ▼
SearchService.hybrid_search(query, category, top_k)
    │
    ├── 1. EmbedClient.embed(query) → 1024dim query_vector
    │
    ├── 2. 并行召回（asyncio.gather）
    │      ├── MilvusClient.search(query_vector, top_n=20, filter=category)
    │      │   → dense_hits[{chunk_id, doc_id, score, category}]
    │      └── ESClient.search(query, top_n=20, filter=category)
    │          → sparse_hits[{chunk_id, doc_id, score, content}]
    │
    ├── 3. _rrf_fuse(dense_hits, sparse_hits, k=60)
    │      score = 1/(60+rank_dense) + 1/(60+rank_sparse)
    │      按 chunk_id 去重，保留高分
    │      → fused[{chunk_id, score, content, doc_id}]
    │
    ├── 4. RerankClient.rerank(query, fused) → reranked[{..., rerank_score}]
    │      失败 → 用 fused score 排序（degraded=True）
    │
    ├── 5. 兜底判断
    │      if reranked[0].rerank_score < 0.35:
    │          → LogService.log(is_fallback=True)
    │          → 返回兜底文案
    │
    ├── 6. 截取 top_k
    │
    ├── 7. 补充溯源信息（DocRepo.get_by_id → title）
    │
    ├── 8. LogService.log(query, hit_counts, scores, latency_ms, trace_id)
    │
    └── 9. 返回 {results, degraded, query_original, trace_id}
```

### 6.4 降级流程

```
检测方式：HTTP 超时 / 连接失败

┌──────────────────────────────────────────────────┐
│ 故障场景              │ 降级路径                  │
├───────────────────────┼─────────────────────────┤
│ Milvus 宕机            │ ES-only 检索（degraded）  │
│ ES 宕机                │ Milvus-only 检索（degraded）│
│ 双侧宕机               │ 返回 A3005 错误           │
│ Rerank 宕机            │ 用 RRF score 排序          │
│ Embedding 宕机         │ 返回 A3003 错误           │
│ score < 0.35          │ 兜底文案，不送 LLM         │
└──────────────────────────────────────────────────┘
```

---

## 7. 错误码（对齐 common/errors/codes.py，已有）

| 错误码 | 描述 | 触发场景 | 处理 |
| :--- | :--- | :--- | :--- |
| A3001 | 文档解析失败 | Parser 异常 | status='failed' |
| A3002 | 文本切片失败 | ChunkService 异常 | status='failed' |
| A3003 | 向量入库失败 | Embedding 服务不可用 | status='failed' |
| A3004 | 检索相关性不足 | max_rerank_score < 0.35 | 返回兜底文案 |
| A3005 | ES 检索失败 | ES 连接失败 | 降级 Milvus-only 或返回错误 |
| A3006 | rerank 失败 | rerank-service 不可用 | 用 RRF score 排序 |
| B0001 | 参数校验失败 | category 不合法 | 400 |
| B0002 | 未认证 | 未登录 | 401 |
| B0003 | 无权限 | 非 ADMIN 上传文档 | 403 |
| B0004 | 资源不存在 | doc_id 不存在 | 404 |

---

## 8. 可配置项

| 键 | 默认值 | 说明 | 来源 |
| :--- | :--- | :--- | :--- |
| KB_CHUNK_SIZE | 500 | 分块大小 | .env |
| KB_CHUNK_OVERLAP | 50 | 分块重叠 | .env |
| KB_CHUNK_MIN_LENGTH | 10 | 最短分块长度 | .env |
| KB_RETRIEVAL_TOP_N | 20 | 每路召回条数 | .env |
| KB_RRF_K | 60 | RRF 融合参数 | .env |
| KB_RERANK_THRESHOLD | 0.35 | 兜底分数阈值 | .env |
| KB_CACHE_TTL | 3600 | 查询缓存 TTL（秒） | .env |
| KB_CACHE_ENABLED | true | 是否启用缓存 | .env |
| RAG_SCORE_THRESHOLD | 0.6 | 全局检索阈值（settings.py 已有） | .env |

---

## 9. 模块独立测试方案

### 9.1 依赖与 Mock

| 依赖 | 真实/Mock | 说明 |
| :--- | :--- | :--- |
| PostgreSQL 15 | 真实 | kb_documents / kb_chunks / kb_search_logs |
| Redis 7 | 真实 | DB 2 |
| Milvus 2.4.13 | 真实 | kb_vectors collection |
| Elasticsearch 8.11.3 | 真实 | kb_bm25 index |
| embedding-service:8100 | Mock | HTTP mock，返回固定 1024dim 向量 |
| rerank-service:8101 | Mock | HTTP mock，返回固定 score |
| LLM | 不需要 | V1 不调用 LLM |
| 其他 6 个服务 | 不需要 | 独立可测 |

### 9.2 测试断言清单

```
 1. 上传 TXT 文档 → kb_chunks 行数 > 0，status='active'
 2. 上传 PDF 文档 → 解析成功，chunks 有内容
 3. 上传 DOCX 文档 → 解析成功，chunks 有内容
 4. 上传 MD 文档 → 解析成功，去除标记符号
 5. 上传不支持格式 → A3001 错误
 6. 分块过滤 → len < 10 的片段被丢弃
 7. Milvus 向量写入 → search 可查到
 8. ES 文档写入 → search 可查到
 9. 混合检索 → 返回 results 非空，含 source 信息
10. RRF 融合 → 两路结果合并去重
11. Rerank → rerank_score 与原始 score 不同
12. 兜底 → rerank_score < 0.35 → results=[], 返回兜底文案
13. 增量删除 → 删后 Milvus/ES 查不到该文档数据
14. 重新入库 → 旧索引删除 + 新索引写入
15. Milvus 宕机 → 降级 ES-only，degraded=True
16. ES 宕机 → 降级 Milvus-only，degraded=True
17. Rerank 宕机 → 用 RRF score 排序，degraded=True
18. 全链路日志 → kb_search_logs 有记录
19. 分类过滤 → category=regulation 不返回 script 类结果
20. trace_id 透传 → 响应包含 trace_id
```

### 9.3 冒烟脚本

```bash
# scripts/smoke/smoke_rag.sh
#!/bin/bash
set -e

BASE_URL="http://localhost:8002/api/v1"

echo "=== RAG Service Smoke Test ==="

# 1. Health check
curl -sf "$BASE_URL/../health" | jq .status

# 2. Upload TXT document
curl -sf -X POST "$BASE_URL/kb/documents" \
  -F "file=@scripts/seed/sample_rules.txt" \
  -F "category=regulation" | jq .data.doc_id

# 3. List documents
curl -sf "$BASE_URL/kb/documents?category=regulation" | jq .data.total

# 4. Search
curl -sf -X POST "$BASE_URL/kb/search" \
  -H "Content-Type: application/json" \
  -d '{"query":"逾期协商","category":"regulation","top_k":3}' | jq .data.results[0].content

echo "=== RAG Smoke Test PASSED ==="
```

---

## 10. V1 → V2 演进路线

| 阶段 | 新增功能 | 依赖 |
| :--- | :--- | :--- |
| V1（本期） | 文档上传+解析+分块+双索引+混合检索+Rerank+兜底+溯源+日志 | PG+Milvus+ES+Redis |
| V2 | 多模态解析（音频ASR+图片OCR）+ Query改写 + 动态路由 + 业务加权 + 行级权限 + 多轮对话 | PaddleOCR + ASR Provider + LLM 改写 |
| V3 | LangSmith 全链路追踪 + 评估模块 + 版本管理 + Prompt 动态配置 | LangSmith + 评估集 |
| V4 | 对齐 DD-03 V2.0 复杂版全部功能 | — |

---

## 11. 开发任务拆分（建议）

| # | 任务 | 产出文件 | 预估时间 |
| :--- | :--- | :--- | :--- |
| T1 | 数据库迁移：扩展 status CHECK + 新建 kb_search_logs 表 | alembic 迁移脚本 | 0.5h |
| T2 | Parser 实现：base + text + md + pdf + docx | app/parsers/*.py | 2h |
| T3 | Repository 实现：doc_repo + chunk_repo | app/repositories/*.py | 1h |
| T4 | VectorStore 实现：milvus_client + es_client + embed_client | app/vectorstore/*.py | 3h |
| T5 | Service 实现：chunk_service + ingest_service | app/services/*.py | 2h |
| T6 | Service 实现：search_service + log_service | app/services/*.py | 3h |
| T7 | API 实现：documents + search + models | app/api/*.py + app/models/*.py | 2h |
| T8 | main.py 扩展 + startup 初始化 | app/main.py | 0.5h |
| T9 | 测试：test_documents + test_search | app/tests/*.py | 2h |
| T10 | 冒烟脚本 + 端到端验证 | scripts/smoke/smoke_rag.sh | 1h |

**总计约 17h（2-3 个工作日）**

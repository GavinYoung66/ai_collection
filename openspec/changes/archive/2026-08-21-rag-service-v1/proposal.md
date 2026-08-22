## Why

电话催收业务急需一个合规知识库检索服务，让坐席能快速查询催收制度、话术、流程、产品规则。当前项目 M0 基础设施（PostgreSQL、Redis、Milvus 2.4.13、Elasticsearch 8.11.3）已全部就绪，但 rag-service 仅有 `/health` 骨架。需要实现最简企业级 RAG 流程跑通：文档上传→解析→分块→双索引（Milvus+ES）→混合检索→Rerank→溯源输出，为后续多模态、复杂路由等 V2 功能打下基础。

## What Changes

- 新增文档管理 API：支持 PDF/DOCX/MD/TXT 上传，自动解析→清洗→分块→向量化→写入 Milvus + ES 双索引
- 新增增量删除/重建索引 API：单文档级删除（删 PG chunks + Milvus vectors + ES docs），禁止全量重建
- 新增混合检索 API：Milvus 向量语义召回 + ES BM25 关键词召回，RRF 融合，Reranker 精排降噪
- 新增低分兜底策略：Top1 rerank_score < 0.35 → 不送 LLM，返回固定兜底文案（对齐 spec 04 强制4：score<0.6 判定无匹配）
- 新增溯源输出：检索结果附带来源文档名、chunk_index、原文片段（对齐 spec 00 强制5：逾期规则等必须调取标准答案库）
- 新增全链路日志表 `kb_search_logs`：记录 query→召回→分数→耗时→trace_id（对齐 spec 06 强制6：完整日志记录）
- 新增 Milvus collection `kb_vectors`（BGE-M3 1024dim）和 ES index `kb_bm25` 初始化逻辑
- 扩展 `kb_documents.status` CHECK 约束：增加 `processing` / `failed` 状态
- 新增分类隔离：通过 `category` 字段（regulation/script/case/other）实现知识分类过滤

## Capabilities

### New Capabilities
- `rag-service/document-ingest`: 文档上传→解析→清洗→分块→向量化→双索引写入→状态管理，支持增量删除和重建
- `rag-service/hybrid-search`: 混合检索（Milvus 向量 + ES BM25 → RRF 融合 → Reranker → 兜底判断 → 溯源输出），含降级策略
- `rag-service/search-logging`: 全链路检索日志记录（query→召回→分数→耗时→trace_id 落库）

### Modified Capabilities
（无现有 spec 需要修改）

## Impact

- **新增代码**：`services/rag-service/app/` 下新增 api/、services/、parsers/、vectorstore/、repositories/、models/ 六层约 20 个文件
- **数据库变更**：扩展 `kb_documents.status` CHECK 约束 + 新建 `kb_search_logs` 表（Alembic 迁移）
- **基础设施依赖**：Milvus（kb_vectors collection 初始化）、Elasticsearch（kb_bm25 index 初始化）、Redis DB 2（缓存 + 熔断标识）
- **外部服务依赖**：embedding-service:8100（BGE-M3 1024dim）、rerank-service:8101（bge-reranker）
- **不涉及其他 6 个服务的代码修改**：rag-service V1 独立可测，不依赖 call/qc/case 等业务服务
- **对齐规范**：统一响应包 `{code:"0",message,data,trace_id}`（spec 02）、三层架构 Controller→Service→Repository（spec 01）、向量 score<0.6 兜底（spec 03/04）、全链路日志（spec 06）、85% 覆盖率 + ruff/bandit/mypy（spec 07）

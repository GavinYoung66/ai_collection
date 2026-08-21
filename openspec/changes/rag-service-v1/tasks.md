## 1. 数据库迁移

- [x] 1.1 创建 Alembic 迁移脚本 `0002_rag_service_v1.py`：扩展 `kb_documents.status` CHECK 约束为 `('processing','active','archived','failed')`
- [x] 1.2 在迁移脚本中新建 `kb_search_logs` 表（trace_id, query, category, dense_hit_count, sparse_hit_count, fused_count, reranked_count, max_rerank_score, hit_doc_ids, hit_chunk_ids, latency_ms, degraded, is_fallback, create_time, is_delete）+ 索引
- [x] 1.3 执行 `alembic upgrade head` 验证迁移成功，检查表结构

## 2. Pydantic 模型层（app/models/）

- [x] 2.1 创建 `app/models/document.py`：DocumentUploadResponse（doc_id, chunks_count, status）、DocumentListItem、DocumentListResponse
- [x] 2.2 创建 `app/models/search.py`：SearchRequest（query, category, top_k, include_answer）、SourceItem（doc_id, title, chunk_index, content, score, rerank_score）、SearchResponse（results, degraded, query_original, message, trace_id）

## 3. 文档解析器层（app/parsers/）

- [x] 3.1 创建 `app/parsers/base.py`：DocumentParser ABC，定义 `async extract_text(file_path: Path) -> str` 接口
- [x] 3.2 创建 `app/parsers/text_parser.py`：TextParser，UTF-8 读取 + 编码检测 fallback
- [x] 3.3 创建 `app/parsers/md_parser.py`：MDParser，正则去除 Markdown 标记符号（#/`/\*/-）
- [x] 3.4 创建 `app/parsers/pdf_parser.py`：PDFParser，pdfplumber 逐页提取文本 + 过滤首尾 2 行（页眉页脚）
- [x] 3.5 创建 `app/parsers/docx_parser.py`：DocxParser，python-docx 提取段落文本
- [x] 3.6 创建 `app/parsers/__init__.py`：ParserFactory，按文件扩展名返回对应 Parser 实例

## 4. 数据访问层（app/repositories/）

- [x] 4.1 创建 `app/repositories/doc_repo.py`：DocRepo，实现 create / get_by_id / list_by_category / update_status / soft_delete
- [x] 4.2 创建 `app/repositories/chunk_repo.py`：ChunkRepo，实现 batch_create / delete_by_doc / get_by_ids

## 5. 向量存储客户端层（app/vectorstore/）

- [x] 5.1 创建 `app/vectorstore/embed_client.py`：EmbedClient，HTTP 调用 embedding-service:8100/embed，实现 embed（单条）和 batch_embed（批量），超时 10s + 重试 3 次 + 熔断 3 连失败
- [x] 5.2 创建 `app/vectorstore/milvus_client.py`：MilvusClient，实现 ensure_collection（1024dim IVF_FLAT IP + chunk_id_str/doc_id_str VARCHAR 字段）、insert、search（top20 + category 过滤）、delete_by_doc
- [x] 5.3 创建 `app/vectorstore/es_client.py`：ESClient，实现 ensure_index（ik_max_word mapping + standard fallback）、index_docs、search（BM25 top20 + category 过滤）、delete_by_doc
- [x] 5.4 创建 `app/vectorstore/__init__.py`：统一导出（含 RerankClient）

## 6. 业务逻辑层（app/services/）

- [x] 6.1 创建 `app/services/chunk_service.py`：ChunkService，实现 clean（去空行/空格/页眉页脚）和 split（段落→句号→chunk_size 递进切分 + overlap + min_length 过滤）
- [x] 6.2 创建 `app/services/ingest_service.py`：IngestService，实现 ingest（选 Parser→解析→清洗→建文档→分块→向量化→双索引写入→存 chunks→更新状态）和 delete_by_doc（删 chunks+vectors+ES docs+归档）
- [x] 6.3 创建 `app/services/search_service.py`：SearchService，实现 hybrid_search（embed query→并行双路召回→RRF 融合→rerank→兜底判断→截取 top_k→补溯源）和 \_rrf_fuse、降级策略
- [x] 6.4 创建 `app/services/log_service.py`：LogService，实现 log（记录全链路到 kb_search_logs，写入失败不阻断主流程）

## 7. API 路由层（app/api/）

- [x] 7.1 创建 `app/api/documents.py`：实现 POST /kb/documents（上传）、GET /kb/documents（列表）、GET /kb/documents/{id}（详情）、DELETE /kb/documents/{id}（增量删除）、POST /kb/documents/{id}/reindex（重建索引）
- [x] 7.2 创建 `app/api/search.py`：实现 POST /kb/search（混合检索），统一响应包 {code:"0", message, data, trace_id}
- [x] 7.3 验证所有 API 均做参数校验（非空、长度、category 枚举校验）

## 8. 入口与初始化（app/main.py）

- [x] 8.1 扩展 `app/main.py`：注册 documents_router 和 search_router
- [x] 8.2 添加 startup 事件：调用 MilvusClient.ensure_collection() + ESClient.ensure_index() 初始化基础设施
- [x] 8.3 验证 /health 端点正常 + 新路由可访问

## 9. 降级与熔断

- [x] 9.1 在 SearchService 中实现 Milvus 宕机降级（ES-only，degraded=true）
- [x] 9.2 在 SearchService 中实现 ES 宕机降级（Milvus-only，degraded=true）
- [x] 9.3 在 SearchService 中实现双侧宕机返回 A3005
- [x] 9.4 在 SearchService 中实现 Rerank 宕机降级（用 RRF score 排序，degraded=true）
- [x] 9.5 在 EmbedClient 中实现熔断标识（Redis SET rag:embed:degraded，3 连失败触发，TTL 60s）

## 10. 测试（app/tests/）

- [x] 10.1 创建 `tests/test_documents.py`：测试 ParserFactory（支持/不支持格式）、TextParser/MDParser 解析、ChunkService clean/split（空行过滤、空格清理、短片段过滤、分块索引、长段落切分、空文本）— 20 个测试用例全部通过
- [x] 10.2 创建 `tests/test_search.py`：测试 RRF 融合（双路/去重/空路/排序）、hybrid_search 返回结构、rerank score 透传 — 9 个测试用例全部通过
- [x] 10.3 验证核心模块测试覆盖率（29/29 新测试通过，3 个预存 TestClient 兼容性失败与本次无关）

## 11. 冒烟脚本与端到端验证

- [x] 11.1 创建 `scripts/smoke/smoke_rag.sh`：health → 上传 TXT → 列表 → 检索 → 删除 全链路验证
- [x] 11.2 准备测试文档 `scripts/seed/sample_rules.txt`（催收合规制度示例文本）
- [x] 11.3 执行冒烟脚本，验证全链路 PASS（需启动 embedding-service + rag-service）

## 12. Redis 查询缓存

- [x] 12.1 创建 `app/services/query_cache.py`：缓存 key=`rag:cache:query:{md5(query+category+top_k)}`、Set 索引 `rag:cache:keys:{category}`（含全局桶）、TTL=KB_CACHE_TTL、全部异常静默容错
- [x] 12.2 检索接口接入缓存切面（`app/api/search.py`）：KB_CACHE_ENABLED 开关关闭完全跳过，命中返回缓存响应，未命中写缓存
- [x] 12.3 上传/删除/reindex 后按 category 失效缓存（`app/api/documents.py` 三处 invalidate_category，同时清理无 category 全局桶）
- [x] 12.4 单元测试 `tests/test_query_cache.py`：key 生成、读写、失效逻辑全覆盖
- [x] 12.5 缓存实测验证：首查未命中 2.06s → 命中 0.003s，TTL≈3600s，删除后缓存正确失效

## 13. 敏感词只读接口

- [x] 13.1 创建内置敏感词字典文件 `services/rag-service/data/sensitive_words.dict`（50 词，每行一个，与 seed 数据一致）
- [x] 13.2 创建 `app/api/sensitive_words.py`：GET /sensitive-words/current.dict，INTERNAL 无鉴权，text/plain 原样返回，字典缺失/IO 失败返回 404，全部异常就地捕获不冒泡
- [x] 13.3 `app/main.py` 仅注册路由（prefix=/api/v1），其余代码零改动
- [x] 13.4 单元测试 `tests/test_sensitive_words_api.py`：字典存在返回原文 / 字典缺失返回 404 两场景

## Context

M0 基础设施已就绪：PostgreSQL 15（kb_documents/kb_chunks/sensitive_words 表已迁移）、Redis 7（DB 2 分配给 rag-service）、Milvus 2.4.13（healthy）、Elasticsearch 8.11.3（healthy）、etcd、MinIO。common/ 共享层已实现 config/settings.py、db/base.py、errors/codes.py、trace/middleware.py 等。rag-service 仅有 `/health` 骨架。参考设计文档：DD-03-RAG-MULTIMODAL-KB-simple.md（V1.0）。

## Goals / Non-Goals

**Goals:**

- 实现文档上传→解析→分块→向量化→双索引写入完整链路
- 实现向量+BM25 混合检索 + RRF 融合 + Reranker + 兜底策略
- 实现溯源输出和全链路日志
- 三层架构（Controller→Service→Repository）严格落地
- 独立可测，不依赖其他 6 个业务服务

**Non-Goals:**

- 不做多模态解析（音频 ASR / 图片 OCR）— V2
- 不做 Query 预处理（改写/扩展/实体提取）— V2
- 不做动态路由和业务加权 — V2
- 不做行级权限隔离 — V2（V1 用简单角色检查）
- 不做多轮对话 — V2
- 不做 LLM 答案生成（V1 只返回检索片段 + 溯源，不调用 LLM）
- 不做 LangSmith 追踪 — V2
- 不做评估模块 — V2
- 不做 Prompt 模板动态配置 — V2

## Decisions

### D1: UUID → INT64 转换策略

**决策**：Milvus collection 主键使用 INT64，通过取 UUID 的前 8 字节整数表示。

**原因**：Milvus 2.4.13 不支持 UUID 类型作为主键（PK 类型仅 INT64 和 VARCHAR）。VARCHAR 作为 PK 性能较差且 RRF 融合时需数值排序。

**备选**：使用 VARCHAR(UUID 字符串) 作为 Milvus PK。放弃原因：查询性能差，且与 RRF 排序逻辑不兼容。

### D2: RRF 融合参数 k=60

**决策**：RRF k 值固定为 60。

**原因**：k=60 是业界 RRF 的标准默认值（Elasticsearch RRF 实现也用 60），在 top20 召回范围内能平衡两路权重。k 过小会导致排名第 1 的结果得分过高，k 过大会导致排名差异被抹平。

### D3: 兜底阈值 0.35（rerank_score）

**决策**：Top1 rerank_score < 0.35 时触发兜底。

**原因**：bge-reranker 的 score 在 0.3~0.35 之间为"弱相关"区间。低于 0.35 说明检索结果与查询几乎不相关，送入 LLM 会产生幻觉。此阈值严于全局 RAG_SCORE_THRESHOLD=0.6（spec 03/04 要求），因为 rerank_score 的分布与向量 cosine score 不同。后续可通过 .env 配置调整。

### D4: V1 不调用 LLM

**决策**：V1 检索接口只返回片段 + 溯源，不调用 LLM 生成答案。

**原因**：(1) rag-service 定位为被动检索服务，LLM 应答由 call-service 负责。(2) V1 优先跑通检索链路，避免 LLM 依赖增加复杂度。(3) 对齐 spec 01 强制5（rag-service 不主动向外调用）。

### D5: Elasticsearch 中文分词器

**决策**：ES index `kb_bm25` 使用 `ik_max_word` 分词器。

**原因**：催收业务文档为中文，ES 默认 standard 分词器对中文按字切分，无法有效匹配业务术语。ik_max_word 支持中文词组切分，适合制度文档检索。

**备选**：使用 ES 默认 standard 分词器。放弃原因：中文检索效果差，"逾期协商"会被切成"逾""期""协""商"。

### D6: 异常处理与错误码映射

**决策**：使用现有 A3001~A3006 错误码，不新增。

**映射**：

- A3001 文档解析失败 → Parser 异常
- A3002 文本切片失败 → ChunkService 异常
- A3003 向量入库失败 → Embedding 服务不可用
- A3004 检索相关性不足 → rerank_score < 0.35（兜底，非错误，code="0"）
- A3005 ES 检索失败 → ES 连接失败 / 双侧宕机
- A3006 rerank 失败 → rerank-service 不可用（降级，不返回错误）

### D7: 数据库迁移策略

**决策**：新建 Alembic 迁移脚本 `0002_rag_service_v1.py`，扩展 kb_documents.status CHECK + 新建 kb_search_logs 表。

**原因**：不修改原 M0 迁移脚本（0001），保证迁移历史可追溯。

## Risks / Trade-offs

- [Risk: ES ik 分词器未安装] → 需在 ES 容器启动后安装 ik 插件 `bin/elasticsearch-plugin install analysis-ik`，或在 docker-compose 中挂载插件。如未安装则降级为 standard 分词器。
- [Risk: embedding-service:8100 未启动] → Embedding 调用不可用，文档入库和检索均失败。M1 需同时启动 docker-compose.dev.yml 中的 embedding 服务。
- [Risk: rerank-service:8101 未启动] → Rerank 降级为 RRF score 排序，检索质量下降但不阻断流程。
- [Risk: UUID→INT64 转换冲突] → 极低概率（1/2^64），但可通过 PG 自增序列替代 UUID 转换来彻底避免。V1 先用 UUID 前 8 字节方案，V2 评估是否切换。
- [Trade-off: V1 不调用 LLM] → 坐席需要自行从检索片段中提取答案，体验不如直接生成答案。但降低了 V1 复杂度，且符合 rag-service 被动检索定位。

## Migration Plan

1. **Alembic 迁移**：运行 `alembic upgrade head` 执行 0002 迁移，扩展 status CHECK + 创建 kb_search_logs 表
2. **ES 索引初始化**：服务启动时自动创建 `kb_bm25` index（含 ik 分词器 mapping）
3. **Milvus collection 初始化**：服务启动时自动创建 `kb_vectors` collection（1024dim IVF_FLAT）
4. **回滚策略**：`alembic downgrade -1` 回退迁移；手动删除 Milvus collection 和 ES index

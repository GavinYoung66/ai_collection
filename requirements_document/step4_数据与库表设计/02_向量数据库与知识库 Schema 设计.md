# 向量数据库与知识库 Schema 设计

> 文档类型：数据设计（规范层）
> 版本：v2.5
> 状态：定稿
> 更新日期：2026-09-10
> 约束声明：本文件为向量库 Schema 单一事实源；遵循 step1/01 全局口径与 openspec/specs/03；向量库与关系库通过 kb_chunks.vector_id 1:1 关联。**一期检索边界**与 step1/04、DD-03 §1.2 同构：M1 仅 Milvus 稠密检索；ES/rerank 为二期预案。

## 修订记录

| 版本 | 日期 | 修订内容 | 修订人 |
|---|---|---|---|
| v0.1 | 2026-08-18 | 初稿骨架，仅内容分类与字段 | — |
| v1.0 | 2026-08-19 | 补 Milvus collection 完整定义、字段 Schema、与 PG 关联、检索约束、索引构建命令 | 规范定稿 |
| v2.0 | 2026-08-20 | 嵌入模型 bge-large-zh-v1.5→BGE-M3（768→1024 维）；新增 Elasticsearch 8 BM25 稀疏检索 Schema；新增 bge-reranker 重排序约束；对齐 DD-03 | 规范优化 |
| v2.1 | 2026-09-09 | P0-5 Milvus 主键类型修复：chunk_id/doc_id 由 INT64 改 VARCHAR(36) 直存 UUID 字符串，消除 PG UUID↔Milvus INT64 不兼容映射（§二字段表、§三关联说明、§六索引构建命令同步；ES mapping 已是 keyword 无需改） | Ellis Guo |
| v2.2 | 2026-09-09 | P1-21 §7.2 ES mapping 移除无 PG 来源的通话/案件维度字段 customer_id/agent_id/risk_tag/expired（决策：移除，非预留）；§7.4 生命周期"新通话/新文档"→"新文档"、expired=true → is_delete='1' 逻辑删除，对齐 kb_chunks | Ellis Guo |
| v2.3 | 2026-09-09 | 批次3（D2-3）：§三 检索流程/§四.3/§四.4/§七.3 KBResult 与喂给 LLM 的清洗后上下文强制携带 source_id（= kb_chunks.doc_id / kb_documents.id）+ 原文片段引用，无 source_id 条目视为"无来源幻觉上下文"禁止拼装；检索日志补 source_id（对齐 step3/03 v1.2 §一.6、step5/04 §3.2 L4 法规来源校验） | Ellis Guo |
| v2.4 | 2026-09-09 | 批次5（O3/D4-2）：§七/§八 开头标注边界——ES BM25 与 bge-reranker 为二期能力（MVP 仅启用 Milvus 稠密检索；启用触发条件：知识库规模 > 5 万篇 或 稠密检索命中率 < 60% 时评估启用）；§7.3 补一期简化版链路（query → Milvus → score<0.6 兜底）；ES/rerank 结构保留为二期预案，不删除一期必需组件 | Ellis Guo |
| v2.5 | 2026-09-10 | 审阅 17：与 DD-03 互相指向——M1 验收只要求 Milvus；DD-03 混合检索设计为非一期强制，不得当 M1 部署条件 | Ellis Guo |

## 一、Collection 定义

| 属性 | 值 |
|---|---|
| Collection 名 | kb_vectors |
| 描述 | 知识库向量索引，与 PostgreSQL kb_chunks 表 1:1 关联 |
| Embedding 模型 | BGE-M3（本地部署，1024 维） |
| 距离度量 | IP（Inner Product，归一化后等价 Cosine） |
| 索引类型 | IVF_FLAT |
| 索引参数 | nlist=1024 |
| 查询参数 | nprobe=16 |

> Embedding 由独立 embedding-service（端口 8100）提供，rag-service 调用，不在业务进程内加载模型。

## 二、字段 Schema

| 字段名 | 类型 | 主键 | 长度/维度 | 说明 |
|---|---|---|---|---|
| chunk_id | VARCHAR | 是 | 36 | 主键，直存 kb_chunks.id 的 UUID 字符串，1:1 等值关联（无类型转换） |
| vector | FLOAT_VECTOR | 否 | 1024 | BGE-M3 产出的 1024 维向量 |
| doc_id | VARCHAR | 否 | 36 | 来源文档 ID（kb_documents.id UUID 字符串），用于过滤 |
| category | VARCHAR | 否 | 64 | 文档分类，对应 kb_documents.category（regulation/script/case/other） |
| version | INT64 | 否 | — | 文档版本号，支持版本切换检索 |
| source | VARCHAR | 否 | 255 | 文档来源标识（文件名或 URL），便于追溯 |

## 三、与 PostgreSQL kb_chunks 的关联

| 维度 | 说明 |
|---|---|
| 主键关联 | Milvus chunk_id（VARCHAR(36)）直存 kb_chunks.id 的 UUID 字符串；kb_chunks.vector_id 存同一 UUID，1:1 等值关联，无类型转换、无 ID 映射 |
| 双向校验 | 入库先写 kb_chunks（vector_id 占位）→ 写 Milvus → 回写 vector_id；任一失败补偿删除 |
| 检索流程 | query → embedding → 并行召回（Milvus 向量检索 top_k + ES BM25 top_k）→ 合并去重 → bge-reranker 精排 → 反查 kb_chunks.content + kb_documents.title 拼装 KBResult；**每条 KBResult 项必须含 source_id（= kb_chunks.doc_id / kb_documents.id）+ 原文片段引用**（score 判定口径见 §四.1） |
| 版本切换 | 切换 kb_documents.version 时，Milvus 检索按 version 字段过滤，旧版本数据软删（不物理删除，便于回滚） |
| 删除联动 | 删除 kb_documents 级联删 kb_chunks，同时按 doc_id 删 Milvus 对应向量（异步任务，失败告警） |

## 四、检索业务约束

1. 检索结果必须做相似度阈值过滤。**score 口径（唯一权威定义）**：召回（Milvus IP 向量检索 + ES BM25）合并去重后，经 bge-reranker 精排输出的最终分数（[0,1] 归一化）；rerank-service 熔断降级时取混合检索归一化融合分数，两口径均可与阈值直接比较。score<0.6 判定无匹配、返回空并标记，调用方走兜底（阈值常量 RAG_SCORE_THRESHOLD=0.6，见 step5/01 §八）；
2. 返回结果做去重，同 doc_id 多分块只保留最高分；
3. 不把原始检索内容直接喂给 LLM，须经清洗加工（截断、去噪、拼装上下文）；清洗加工后喂给 LLM 的每个上下文条目**必须携带 source_id（= kb_chunks.doc_id / kb_documents.id）标注 + 原文片段引用**，供法规来源溯源与后置 L4 法规来源校验（step5/04 §3.2）；缺失 source_id 的条目视为"无来源幻觉上下文"，禁止拼装进 Prompt；
4. 检索日志记录 query、top-k、分数、命中 id、source_id，送入数据中台（etl_task_logs + llm_call_logs），支撑审计追溯；
5. 混合检索：Milvus 稠密向量检索 + Elasticsearch BM25 稀疏检索，并行召回后合并去重，再经 bge-reranker 二次精排，单一来源不足时降级。

## 五、知识库内容分类

| category | 内容 | 维护方 |
|---|---|---|
| regulation | 催收政策、合规规则、红线标准答案 | rag-service |
| script | 业务话术参考、标准 FAQ、合规兜底话术 | rag-service（review-service 沉淀反哺） |
| case | 优质案例提炼（经人工审核入库） | review-service |
| other | 其他参考资料 | rag-service |

> 兜底话术、标准答案统一在此库维护，禁止硬编码在业务代码（详见 step1/03 兜底话术表）。

## 六、索引构建命令（参考）

```python
from pymilvus import Collection, CollectionSchema, FieldSchema, DataType

fields = [
    FieldSchema(name="chunk_id", dtype=DataType.VARCHAR, is_primary=True, max_length=36),
    FieldSchema(name="vector",   dtype=DataType.FLOAT_VECTOR, dim=1024),
    FieldSchema(name="doc_id",   dtype=DataType.VARCHAR, max_length=36),
    FieldSchema(name="category", dtype=DataType.VARCHAR, max_length=64),
    FieldSchema(name="version",  dtype=DataType.INT64),
    FieldSchema(name="source",   dtype=DataType.VARCHAR, max_length=255),
]
schema = CollectionSchema(fields=fields, description="知识库向量索引，与 PostgreSQL kb_chunks 1:1")
collection = Collection(name="kb_vectors", schema=schema)
collection.create_index(
    field_name="vector",
    index_params={"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 1024}},
)
```

rag-service 启动时确保 collection 与索引存在；检索参数 nprobe=16 经 `Settings.milvus_nprobe` 注入，可配置。

---

## 七、Elasticsearch 8 BM25 稀疏检索 Schema

> **[一期边界标注（批次5 O3/D4-2；审阅 17）]**：一期（MVP / **M1 验收**）仅启用 **Milvus 稠密检索**；ES BM25 稀疏检索为**二期能力**（开关默认关）。DD-03 保留混合检索设计段落，但标「非一期强制」，**不得当作 M1 必部署 ES**。启用触发条件：知识库规模 > 5 万篇 或 稠密检索命中率 < 60% 时评估启用。MVP 阶段可不部署 ES、不下发 `kb_chunks_bm25` 索引（本 schema 结构保留为二期预案）。权威边界亦见 step1/04「检索边界」。

### 7.1 索引定义

| 属性 | 值 |
|---|---|
| 索引名 | `kb_chunks_bm25` |
| 描述 | 知识库文本 BM25 稀疏检索索引，与 PostgreSQL kb_chunks 表 1:1 关联 |
| 分词器 | `ik_max_word`（中文分词）+ `lowercase` |
| 检索方式 | BM25（内置评分） |

### 7.2 字段映射（Mapping）

```json
{
  "properties": {
    "chunk_id": { "type": "keyword" },
    "content": { "type": "text", "analyzer": "ik_max_word", "search_analyzer": "ik_smart" },
    "doc_id": { "type": "keyword" },
    "category": { "type": "keyword" },
    "version": { "type": "long" },
    "source": { "type": "keyword" },
    "created_at": { "type": "date" }
  }
}
```

### 7.3 与 Milvus 的协同检索流程

```
query → QueryProcessor 预处理（改写+实体提取）
  → 并行召回：
    a) Milvus 稠密向量检索（BGE-M3 1024dim, top20, IP）
    b) ES BM25 稀疏检索（ik_max_word, top20）
  → 合并 + 去重（按 chunk_id）
  → bge-reranker 二次精排（rerank-service:8101）
  → 业务加权 + 动态路由
  → ContextCompressor 压缩
  → score<0.6 → 返回空 + 拒绝生成标记
```

> 注：流程末尾判定的 score 即 bge-reranker 精排输出（[0,1] 归一化）或降级时的混合检索融合分数，与 §四.1 阈值口径一致；"业务加权"需保证输出仍归一化至 [0,1] 方可与 RAG_SCORE_THRESHOLD 同口径比较。
>
> 溯源：ContextCompressor 压缩输出的每个上下文条目与最终 KBResult 均须保留 source_id（= kb_chunks.doc_id / kb_documents.id）+ 原文片段引用，随 Prompt 传给 LLM 并随 llm_call_logs.prompt 落库可溯源，见 §三/§四。

> **一期简化版（批次5 O3/D4-2）**：一期（MVP）检索链路 = `query → Milvus 稠密检索（BGE-M3, top20, IP）→ score<0.6 走兜底`，**不经过 ES BM25 与 bge-reranker**（ES+reranker 为二期扩展路径，边界见 §七/§八）；无 reranker 阶段 score 取 Milvus 召回归一化分数与 RAG_SCORE_THRESHOLD 同口径比较，启用 reranker 后回归 §四.1 精排口径。

### 7.4 索引生命周期

- 新文档解析后，同步写入 Milvus + ES（双写）
- 旧文档逻辑删除（kb_chunks.is_delete='1'），检索时自动过滤
- 删除联动：删除 kb_documents → 级联删 kb_chunks → 按chunk_id 删 Milvus + ES

---

## 八、bge-reranker 重排序约束

> **[一期边界标注（批次5 O3/D4-2）]**：bge-reranker 重排序为**二期能力**；一期（MVP）仅启用 Milvus 稠密检索、不调用 rerank-service。启用触发条件：知识库规模 > 5 万篇 或 稠密检索命中率 < 60% 时评估启用（口径同 §七）。MVP 阶段可不部署/不下发 rerank-service。

| 属性 | 值 |
|---|---|
| 服务名 | rerank-service |
| 端口 | 8101 |
| 模型 | bge-reranker-large |
| 输入 | query + candidates（list[chunk_id, content]） |
| 输出 | list[{chunk_id, rerank_score}]，score 范围 [0, 1] |
| 调用方 | rag-service |
| 降级策略 | rerank-service 熔断 → 跳过重排序，直接使用混合检索归一化融合分数（score 判定口径随降级切换为融合分，仍与 RAG_SCORE_THRESHOLD=0.6 同口径可比，见 §四.1） |

# 子系统详细设计 #03：RAG 知识库服务（简化版 V1，规范对齐版 V1.2）

> 子系统编号：第 3 号
> 服务名：`rag-service`
> 监听端口：**8002**
> 里程碑归属：**M1（最小闭环 RAG 流程跑通）**
> 上游约束：step1/01~03 全局口径、step3/01~03 架构与降级、step4/01~04 数据设计、step5/01~04 技术规范、step6/01~02 异常容错与可观测性
> 复杂版参考：DD-03-RAG-MULTIMODAL-KB.md（V2.0，含多模态/复杂路由/Query改写/LangSmith，本期不实现）
> 本文档状态：**与 rag-service-v1 已实现代码对齐**（缓存/限流/权限/敏感词/降级均已落地并通过全量测试）

---

## 0. 文档修订记录

| 版本 | 日期       | 修订人        | 修订内容                                                                                                                                                                                                                                                            |
| :--- | :--------- | :------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| V1.0 | 2026-08-20 | 规范优化      | 对齐项目实际技术栈重写：PG+Milvus+ES+Redis；补充完整目录结构、业务流程、函数签名；明确 V1 范围与延后清单                                                                                                                                                            |
| V1.1 | 2026-08-21 | AI 产品架构师 | 评审优化：删除代码骨架冗余；补齐边界契约；修正敏感词契约（text/plain）；深化 §10 V1→V2 演进与 §11 任务拆分                                                                                                                                                          |
| V1.2 | 2026-08-21 | AI 产品架构师 | **全局规范对齐版**：逐条核对 step1/03/04/05/06 规范，修正 B0001 状态码、双阈值闸门（0.6/0.35）、Milvus/ES schema 差异、敏感词数据源差异、Redis key 收录状态、kb_search_logs 类型规范冲突、数据中台对接与监控；全部差异以"规范契约 / V1 实现 / 差异与演进"三段式标注 |

### 0.1 规范对齐声明（单一事实源）

| 主题                            | 权威文件 | 本 PRD 引用要点                                                                      |
| :------------------------------ | :------- | :----------------------------------------------------------------------------------- |
| 错误码全集（84 条）             | step6/01 | A3xxx 知识库域 + B0001~B0010；禁止私自编号                                           |
| 统一响应包 / HTTP 状态码 / 分页 | step5/02 | `{code,message,data,trace_id}`；业务错误 200、系统错误 5xx、参数 422；page/page_size |
| 向量库/ES/Rerank Schema         | step4/02 | kb_vectors 6 字段、kb_chunks_bm25、bge-reranker 约束                                 |
| Redis key / DB 分配 / Pub-Sub   | step4/03 | rag 前缀 DB2、键命名冒号分隔、`kb.sensitive_updated`                                 |
| KB 表 DDL / 类型规范            | step4/01 | kb_documents/kb_chunks/sensitive_words；枚举用 VARCHAR+CHECK                         |
| 降级与兜底分级                  | step3/03 | L1 LLM / L2 检索无果(score<0.6) / L3 合规；兜底话术入 script 标准答案库              |
| 重试/超时/熔断                  | step6/01 | 5s 超时、3 次指数退避（1s/2s/4s）、3 连失败熔断                                      |
| 埋点/监控/告警                  | step6/02 | rag 监控项：检索命中率<60%、Embedding 队列积压>100、版本切换失败>0                   |

---

## 1. 模块概览

### 1.1 定位

面向电话催收业务的**轻量企业级 RAG 后端服务**。坐席输入业务问题，系统从私有合规文档中检索相关知识片段，返回带溯源引用的检索结果（不生成话术，由 call-service 消费片段）。

### 1.2 V1 能力边界（已实现，验收基线）

| #   | 能力                | 说明                                                                       | 状态 |
| :-- | :------------------ | :------------------------------------------------------------------------- | :--- |
| 1   | 文档上传 + 解析入库 | PDF/DOCX/MD/TXT，解析→清洗→分块→向量化→双索引（Milvus+ES）                 | ✅   |
| 2   | 增量更新/删除       | 单文档删除仅操作该文档的向量/索引/chunks，禁止全量重建                     | ✅   |
| 3   | 混合检索            | Milvus 向量 + ES BM25 → RRF(k=60) 融合 → Rerank 精排                       | ✅   |
| 4   | Reranker 重排序     | rerank-service:8101；宕机降级 RRF score 排序                               | ✅   |
| 5   | 低分兜底（防幻觉）  | Top1 rerank_score < 0.35 → 兜底文案，不送 LLM                              | ✅   |
| 6   | 溯源引用            | 结果附 doc_id/title/chunk_index/content/score/rerank_score                 | ✅   |
| 7   | 全链路日志          | 每次检索落库 kb_search_logs（15 字段）                                     | ✅   |
| 8   | 分类隔离            | category ∈ {regulation, script, case, other} 过滤                          | ✅   |
| 9   | Redis 高频查询缓存  | `rag:cache:query:{md5}` + 按分类失效 + 容错降级                            | ✅   |
| 10  | 接口限流            | Redis 按用户维度固定窗口，超限 429+B0006                                   | ✅   |
| 11  | 接口权限校验        | 上传/删除/reindex 需 ADMIN/SUPERVISOR，401+B0002 / 403+B0003               | ✅   |
| 12  | 敏感词只读接口      | GET /sensitive-words/current.dict（INTERNAL，text/plain）                  | ✅   |
| 13  | 降级/熔断           | Milvus→ES-only / ES→Milvus-only / 双挂→A3005 / Rerank→RRF / Embedding 熔断 | ✅   |

### 1.3 V1 明确不做（延后清单见 §10）

多模态解析（ASR/OCR）、Query 改写、动态路由、业务加权、行级权限、多轮对话、版本管理+灰度、LangSmith、评估模块、Prompt 动态配置、敏感词 CRUD。延后原因与版本规划统一见 §10。

### 1.4 AI 边界约束

- rag-service **只提供被动检索**，不主动向外调用服务（不调 case-service / data-platform）
- 不修改案件状态（归 case-service）；不做质检打分（归 qc-service）
- **不生成话术**：V1 返回检索片段 + 溯源，不接 LLM（`include_answer` 已移除）
- 接口调用顺序：**先权限校验 → 再限流 → 再执行业务逻辑**

---

## 2. 技术栈（已落地，对齐 step1/01、step4/02）

| 组件           | 选型                         | 说明                                                                                 |
| :------------- | :--------------------------- | :----------------------------------------------------------------------------------- |
| Web 框架       | FastAPI + Uvicorn            | 已有骨架                                                                             |
| 业务数据库     | PostgreSQL 15                | kb_documents / kb_chunks / kb_search_logs / sensitive_words                          |
| 向量数据库     | Milvus 2.4.13                | collection `kb_vectors`（BGE-M3 1024dim，IVF_FLAT nlist=1024，metric=IP，nprobe=16） |
| 关键词检索     | Elasticsearch 8.11.3         | index `kb_bm25`（ik_max_word；规范名为 kb_chunks_bm25，差异见 §4.3）                 |
| 缓存/限流/熔断 | Redis 7（DB 2，前缀 `rag:`） | 查询缓存 + 限流计数 + 熔断标识                                                       |
| Embedding      | embedding-service:8100       | BGE-M3 1024dim，5s 超时/3 次退避/3 连失败熔断                                        |
| Reranker       | rerank-service:8101          | bge-reranker-large，score∈[0,1]                                                      |
| LLM            | **V1 不接入**                | 兜底直接返回文案，不送 LLM                                                           |
| 追踪           | X-Trace-Id                   | common/trace/middleware.py，`trace_<ulid>`                                           |
| 错误码         | A3001~A3006 + B0001~B0006    | 对齐 step6/01，不新增                                                                |

**健康检查依赖矩阵**（对齐 step1/01）：`GET /health` 依赖 PostgreSQL、Milvus、Redis、embedding-service 四者探活。

---

## 3. 目录结构与文件职责

```
services/rag-service/
├── app/                          # 应用主目录（六层架构：api → models → parsers → repositories → services → vectorstore）
│   ├── main.py                   # FastAPI 入口：路由注册 + 鉴权/限流/异常 handler + startup 初始化 Milvus/ES + /health
│   ├── api/                      # 路由层（Controller）：先权限 → 再限流 → 再业务
│   │   ├── health.py             # GET /health：依赖探活（PG/Milvus/Redis/embedding）
│   │   ├── deps.py               # 依赖注入（db/redis/各客户端）+ success_response 统一响应包 + save_upload_file
│   │   ├── documents.py          # 上传/列表/详情/删除/reindex（权限 + 限流 + 缓存失效）
│   │   ├── search.py             # 混合检索（限流 200/min + 缓存切面 + 请求级超时）
│   │   └── sensitive_words.py    # GET /sensitive-words/current.dict（INTERNAL，text/plain 原样返回）
│   ├── models/                   # Pydantic 模型层
│   │   ├── orm.py                # SQLAlchemy ORM：kb_documents / kb_chunks / kb_search_logs
│   │   ├── document.py           # DocumentUploadResponse / DocumentListItem / DocumentListResponse
│   │   └── search.py             # SearchRequest / SourceItem / SearchResponse
│   ├── parsers/                  # 文档解析层（ABC + ParserFactory 按扩展名分发）
│   │   ├── base.py               # DocumentParser ABC：async extract_text()
│   │   ├── text_parser.py        # TXT：UTF-8 + 编码检测 fallback
│   │   ├── md_parser.py          # MD：正则去除 #/`/*/-
│   │   ├── pdf_parser.py         # PDF：pdfplumber，页文本>4 行去首尾各 2 行（页眉页脚）
│   │   └── docx_parser.py        # DOCX：python-docx 段落提取，空段过滤
│   ├── repositories/             # 数据访问层（Repository）
│   │   ├── doc_repo.py           # kb_documents：create/get/list/update_status/soft_delete/chunks 计数
│   │   └── chunk_repo.py         # kb_chunks：batch_create/delete_by_doc/get_by_ids
│   ├── services/                 # 业务逻辑层（Service）
│   │   ├── chunk_service.py      # 分块：段落→句号→chunk_size，overlap=50，过滤 len<10
│   │   ├── ingest_service.py     # 入库编排（10 步流程 §6.1）；PG 成功但索引失败→回滚+failed+A3003
│   │   ├── search_service.py     # 检索编排：双路并行→RRF→Rerank→双闸门兜底；降级 §6.4
│   │   ├── log_service.py        # kb_search_logs 落库（写入失败不阻断主流程）
│   │   ├── query_cache.py        # 查询缓存：key=md5(query+category+top_k)，分类 Set 索引，禁 KEYS
│   │   ├── rate_limit.py         # 限流：Redis INCR+EXPIRE，X-User-Id 优先/IP 回退，429+B0006
│   │   └── require_role.py       # 权限：Bearer JWT（common.security.rbac），401+B0002 / 403+B0003
│   └── vectorstore/              # 向量存储客户端层
│       ├── milvus_client.py      # kb_vectors：UUID→INT64 + str 冗余字段，insert/search/delete_by_doc
│       ├── es_client.py          # kb_bm25：ik_max_word（缺插件回退 standard），index/search/delete_by_doc
│       ├── embed_client.py       # embedding-service:8100：超时/重试/熔断（rag:embed:degraded TTL 60s）
│       └── rerank_client.py      # rerank-service:8101：精排，失败降级 RRF score 排序
├── data/
│   └── sensitive_words.dict      # 敏感词字典（每行一词，与 seed 一致；V1 只读接口数据源，差异见 §5.3）
├── tests/                        # 12 个测试文件（解析/缓存/限流/权限/降级/双闸门/分页/reindex 端到端）
│   ├── conftest.py               # 共享 fixture（TestClient / DB / Redis mock）
│   ├── test_health.py            # /health 探活
│   ├── test_documents.py         # ParserFactory/Text/MD 解析 + ChunkService clean/split（20 用例）
│   ├── test_pdf_docx_parser.py   # PDF/DOCX 解析（页眉页脚过滤）
│   ├── test_search.py            # RRF 融合（双路/去重/空路/排序）+ 检索响应结构
│   ├── test_search_degradation.py# Milvus/ES/Rerank 降级场景
│   ├── test_gate_threshold.py    # 双闸门兜底阈值（rerank 0.35 / dense 0.6）
│   ├── test_query_cache.py       # 缓存 key 生成/读写/失效
│   ├── test_rate_limit.py        # 限流阈值/429+统一响应包/用户维度隔离/Redis 故障放行（4 用例）
│   ├── test_require_role.py      # 401+B0002 / 403+B0003 / 角色大小写不敏感（6 用例）
│   ├── test_sensitive_words_api.py # 字典只读接口（存在返回原文 / 缺失返回 404）
│   ├── test_pagination.py        # 列表分页（page / page_size / size 兼容回退）
│   └── test_reindex.py           # reindex 端到端
└── Dockerfile                    # 镜像：公共基础镜像 + 全局 requirements 安装
```

---

## 4. 数据库与存储

### 4.1 PostgreSQL（DDL 单一事实源为 step4/01，本表为差异对照）

- **kb_documents**（status 已扩展）：
  - ▶ **规范契约**（step4/01）：status CHECK `('active','archived')`，category VARCHAR(50) + CHECK 四值
  - ▶ **V1 实现**：status CHECK 已扩展为 `('processing','active','archived','failed')`
  - ▶ **差异与演进**：扩展属合理演进（入库过程状态），需同步 step4/01 DDL 收录
- **kb_chunks**：id/doc_id(FK CASCADE)/chunk_index/content/vector_id(VARCHAR100)/category/version/公共字段；`UNIQUE(doc_id, chunk_index)` + 唯一索引 vector_id
- **kb_search_logs**（V1 迁移 0002 新增）：
  - ▶ **规范契约**（step4/01 §1.2）：枚举/布尔一律 VARCHAR+CHECK，**禁止 BOOLEAN**；本表未收录 step4/01 19 表清单
  - ▶ **V1 实现**：15 字段（trace_id/query/category/五个计数/max_rerank_score/hit_doc_ids/hit_chunk_ids/latency_ms/degraded/is_fallback/时间/is_delete），其中 degraded/is_fallback 用 BOOLEAN
  - ▶ **差异与演进**：BOOLEAN 违反全局类型规范，建议 V2 迁移改 VARCHAR('0','1') + CHECK；表定义待 step4/01 收录避免双源漂移
- **sensitive_words**（规范表）：word(VARCHAR100 UNIQUE)/category(threat/abuse/induce)/severity(high/medium/low)/status(active/disabled)/description —— V1 只读接口数据源为文件而非本表，见 §5.3

### 4.2 Milvus `kb_vectors`

| 字段         | 类型               | 说明                           |
| :----------- | :----------------- | :----------------------------- |
| chunk_id     | INT64 PK           | kb_chunks.vector_id 的数字映射 |
| vector       | FLOAT_VECTOR(1024) | BGE-M3                         |
| doc_id       | INT64              | 文档 ID 数字映射               |
| category     | VARCHAR(50)        | 分类过滤（规范为 64）          |
| chunk_id_str | VARCHAR(36)        | UUID 字符串冗余（检索溯源用）  |
| doc_id_str   | VARCHAR(36)        | UUID 字符串冗余                |

- ▶ **规范契约**（step4/02）：字段为 chunk_id/vector/doc_id/category(VARCHAR64)/**version(INT64)/source(VARCHAR255)**
- ▶ **差异与演进**：V1 以 chunk_id_str/doc_id_str 冗余替代 version/source；**version/source 为规范预留**，V2 增列需一次性重建索引（见 §10.3）

### 4.3 Elasticsearch `kb_bm25`

| 字段     | 类型                                     | 说明          |
| :------- | :--------------------------------------- | :------------ |
| chunk_id | keyword                                  | UUID 字符串   |
| content  | text（ik_max_word，缺插件回退 standard） | BM25 检索字段 |
| doc_id   | keyword                                  | 文档 ID       |
| category | keyword                                  | 分类过滤      |

- ▶ **规范契约**（step4/02 §七）：索引名 `kb_chunks_bm25`；mapping 另含 version/source/customer_id/agent_id/risk_tag/created_at/expired
- ▶ **差异与演进**：索引名与规范不一致（实现 kb_bm25，规范 kb_chunks_bm25），**待统一**；额外字段为 V2 业务加权/行级权限预留

### 4.4 Redis（DB 2，前缀 `rag:`）

| Key                                           | 结构         | TTL                | 用途                                                                |
| :-------------------------------------------- | :----------- | :----------------- | :------------------------------------------------------------------ |
| `rag:cache:query:{md5(query+category+top_k)}` | STRING(JSON) | KB_CACHE_TTL=3600s | 高频查询缓存（命中返回序列化响应 dict）                             |
| `rag:cache:keys:{category}`                   | SET          | 随成员             | 分类缓存索引（失效时读取删除，禁 KEYS；category 空→全局桶）         |
| `rag:ratelimit:{bucket}:{user}`               | STRING(INT)  | 60s                | 固定窗口限流计数（bucket=upload/list/detail/delete/reindex/search） |
| `rag:embed:degraded`                          | STRING       | 60s                | Embedding 熔断标识（3 连失败置位）                                  |
| `rag:rerank:degraded`                         | STRING       | 60s                | Rerank 熔断标识                                                     |

- ▶ **规范契约**（step4/03）：key 清单为 `rag:bm25_index:<category>`（BM25 索引 pickle，已被 ES 取代）/`rag:sensitive_words:version`/`rag:documents`；命名格式 `<prefix>:<entity>:<id>`，分隔符统一冒号
- ▶ **差异与演进**：V1 新增 5 个 key 均不在规范清单，**待 step4/03 收录**；`rag:embed:degraded`/`rag:rerank:degraded` 用下划线（与 step6-01 `{service}:llm_degraded` 同风格，但违反 step4/03 冒号规则——规范自身冲突，V2 统一）；敏感词版本号 `rag:sensitive_words:version` V1 未实现（见 §5.3）

### 4.5 数据中台对接与监控（对齐 step4/02 §四.4、step6/02 §5.2）

- 检索日志：kb_search_logs 本地落库后，**经 data-platform 订阅/ETL 入 dw 层**（检索日志源 etl_task_logs/llm_call_logs），rag-service 不主动推送（被动服务）
- rag 监控指标（step6/02 定义，V1 需上报埋点）：① 检索命中率 <60%（日维度告警）；② Embedding 队列积压 >100；③ 知识库版本切换失败 >0（V2 启用版本后）
- 埋点事件：所有降级/熔断/超时事件含 trace_id 记 structlog 结构化日志，接入告警渠道（Redis Pub-Sub `system.alert.*` + audit_logs）

---

## 5. 对外接口

统一前缀 `/api/v1`，统一响应包 `{"code":"0","message":str,"data":any|null,"trace_id":str}`（对齐 step5/02）。
**通用约定**：先权限 → 再限流 → 再业务；敏感词接口为 INTERNAL，不接权限/限流。

### 5.1 文档管理

| 方法   | 路径                             | 权限             | 限流   | 说明                             |
| :----- | :------------------------------- | :--------------- | :----- | :------------------------------- |
| POST   | `/kb/documents`                  | ADMIN/SUPERVISOR | 20/min | 上传（multipart/form-data）      |
| GET    | `/kb/documents`                  | ALL              | 60/min | 列表分页（category/status 过滤） |
| GET    | `/kb/documents/{doc_id}`         | ALL              | 60/min | 详情（元数据 + chunks 数量）     |
| DELETE | `/kb/documents/{doc_id}`         | ADMIN/SUPERVISOR | 20/min | 增量删除                         |
| POST   | `/kb/documents/{doc_id}/reindex` | ADMIN/SUPERVISOR | 10/min | 重新解析入库                     |

**上传请求**：`file`（≤20MB，超限→B0001）+ `category`（四值之一，非法→B0001）。
**上传响应**：`{doc_id, chunks_count, status}`。
**列表分页**：`page`（从 1 起）、`page_size`（默认 20，最大 100）——▶ 对齐 step5/02（实现字段 size 待统一为 page_size，见附录 A.A8）。

**边界契约**：

- 格式白名单：`.pdf/.docx/.md/.txt`；其他（含 .exe）→ A3001；空文件/损坏加密 PDF → A3001，status=failed
- 同名策略：每次上传生成新 doc_id，不覆盖
- 删除不存在 doc_id → B0004；重复删除幂等（已 archived 直接成功）
- reindex 对 failed/archived 文档 → B0004（状态机约束）

### 5.2 混合检索

| 方法 | 路径         | 权限                | 限流    | 说明     |
| :--- | :----------- | :------------------ | :------ | :------- |
| POST | `/kb/search` | ALL（跳过权限校验） | 200/min | 混合检索 |

**请求体**：`{query: str, category?: str, top_k: int=5}`；**入参边界**：top_k∈[1,100]、category 四值、query 非空（非法→B0001，HTTP 422）
**响应体**：`{results:[{doc_id,title,chunk_index,content,score,rerank_score}], degraded, query_original, message, trace_id}`

**双闸门兜底（对齐 step1/01 全局阈值 + V1 实现）**：

- 闸门 1（对齐规范）：检索融合 score < **0.6**（RAG_SCORE_THRESHOLD）→ 判定知识库无匹配，标记降级
- 闸门 2（V1 实现）：Top1 rerank_score < **0.35**（KB_RERANK_THRESHOLD）→ 返回兜底文案
- ▶ V1 已实现闸门 2；闸门 1 配置已存在（settings.RAG_SCORE_THRESHOLD=0.6）但未接入检索流程，**待补**（见 §11.2 整改任务）
- 兜底响应：`results:[]` + `message` 固定文案，**code 仍为 "0"**（业务成功语义）
- **缓存契约（V1 缺陷，整改见 V1-T19/T20）**：
  - 兜底/降级响应（`is_fallback` 或 `degraded=true`）**禁止写查询缓存**，否则故障/无匹配结果被缓存 1 小时，知识库更新后仍持续返回旧结果
  - 缓存命中响应**必须落 kb_search_logs**（标记 cache_hit=true），否则命中率监控指标失真（step6/02 检索命中率<60% 告警将无法反映真实检索质量）

### 5.3 敏感词只读接口

| 方法 | 路径                            | 权限                      | 说明         |
| :--- | :------------------------------ | :------------------------ | :----------- |
| GET  | `/sensitive-words/current.dict` | INTERNAL（不接权限/限流） | 返回字典原文 |

**契约**：`Content-Type: text/plain`，原样返回，不包装 JSON；字典缺失/IO 失败 → HTTP 404（不抛 500、不触发全局 handler）；仅输出不做拦截。

- ▶ **规范契约**（step4/01 + step4/03）：敏感词存 sensitive_words 表（word/category/severity/status），版本号 `rag:sensitive_words:version` 自增，更新后 PUBLISH `kb.sensitive_updated`，qc-service 60s 内拉取重建 AC 自动机
- ▶ **V1 实现**：返回内置 `data/sensitive_words.dict` 文件（50 词，与 M0 seed 表数据一致），无版本号/Pub-Sub
- ▶ **差异与演进**：数据源与规范不一致（文件 vs DB 表）。V1 以文件简化交付；**V2 必须改为读 sensitive_words 表 + 版本号 + 发布热刷新事件**（qc-service 依赖该机制，见 §10.2 V2-T9）。消费方：qc-service（语音质检比对）、call-service。

---

## 6. 业务流程

### 6.1 文档入库（10 步，含多存储回滚）

```
上传(file, category) → 权限/限流 → 格式/大小校验 → Parser 选择 → extract_text（解析超时 30s）
→ clean → INSERT kb_documents(processing) → split（空→failed+A3002）
→ batch_embed（失败→failed+A3003）→ Milvus.insert + ES.index_docs
→ INSERT kb_chunks → UPDATE status=active → invalidate_cache(category) → 返回
```

**多存储异常回滚契约**：

1. PG 写入成功、Milvus/ES 写入异常 → status=failed + 回滚已写入索引 + A3003
2. 禁止"PG=active 但向量库无索引"脏状态
3. 仅 PG 失败 → 不执行 Milvus/ES 写入

- ▶ **与 step6/01 §6.1 差异**：规范口径为"Embedding 失败 → 跳过向量化 + 告警"（降级入库）；V1 采用"失败置 failed + A3003"（防无向量脏数据）。V2 可选支持降级跳过向量化（仅文本索引）

### 6.2 增量删除

```
DELETE /documents/{doc_id} → 权限/限流 → 删 PG chunks → 删 Milvus vectors
→ 删 ES docs → status=archived → invalidate_cache(category)
```

### 6.3 混合检索

```
POST /search → 限流 → embed(query) → 并行召回(Milvus+ES, 各 top20, category 过滤)
→ RRF(k=60) 融合去重 → Rerank 精排 → 双闸门兜底(0.6/0.35)
→ 截取 top_k → 补 title 溯源 → 落库 kb_search_logs → 写缓存 → 返回
```

- ▶ **与 step4/02 §四.2 差异**：规范要求"同 doc_id 多分块只保留最高分"；V1 实现仅按 chunk_id 去重。V2 对齐（见 §11.3 V2-T10）

### 6.4 降级矩阵（5 重 + 扩展，对齐 step6/01、step3/03）

| 故障场景                 | 降级路径             | 响应/错误码              | 告警       |
| :----------------------- | :------------------- | :----------------------- | :--------- |
| Milvus 宕机              | ES-only 检索         | degraded=true            | Warning    |
| ES 宕机                  | Milvus-only 检索     | degraded=true            | Warning    |
| 双侧宕机                 | 不检索               | A3005                    | Critical   |
| Rerank 宕机              | RRF score 排序       | degraded=true            | Warning    |
| Embedding 宕机（入库侧） | 中止入库             | A3003, status=failed     | Critical   |
| Embedding 宕机（检索侧） | 中止检索             | A3003                    | Critical   |
| Redis 故障（缓存/限流）  | 静默绕过             | 正常返回                 | Warning    |
| 检索融合分 < 0.6         | 判定无匹配（闸门 1） | 标记 degraded（V1 待补） | 命中率指标 |
| rerank_score < 0.35      | 兜底文案（闸门 2）   | code=0, results=[]       | —          |

降级总原则（对齐 step3/03）：检索为空/失败**禁止抛异常给终端**，调用方（call-service）走 FAQ 兜底。

---

## 7. 错误码（对齐 step6/01，禁止新增）

| 错误码 | 描述         | HTTP    | 触发场景                               | 处理              |
| :----- | :----------- | :------ | :------------------------------------- | :---------------- |
| A3001  | 文档解析失败 | 200     | Parser 异常/格式不支持/空文件/损坏加密 | status=failed     |
| A3002  | 文本切片失败 | 200     | 分块后无有效内容                       | status=failed     |
| A3003  | 向量入库失败 | 200     | Embedding 服务不可用（入库/检索侧）    | failed / 检索中止 |
| A3005  | 检索降级失败 | 200     | Milvus+ES 双侧宕机                     | 返回错误          |
| B0001  | 参数校验失败 | **422** | category/top_k/query 非法、文件超限    | —                 |
| B0002  | 未认证       | 401     | 未携带/无效 JWT                        | —                 |
| B0003  | 无权限       | 403     | 角色不足                               | —                 |
| B0004  | 资源不存在   | 404     | doc_id 不存在                          | —                 |
| B0006  | 请求频率超限 | 429     | 超过接口阈值                           | —                 |

- ▶ A3004（相关性不足）与 A3006（rerank 失败）不作为对外返回码：兜底走 code=0+message（对齐 step6-01"业务错误统一 200 封装"），rerank 失败走降级（degraded=true）
- ▶ 系统异常（B0007/B0008/B0009）由全局 handler 统一处理，本服务不自行返回

---

## 8. 可配置项（对齐 step6/01 重试超时、step5/01 环境）

| 键                             | 默认值     | 说明                                                     |
| :----------------------------- | :--------- | :------------------------------------------------------- |
| KB_CHUNK_SIZE                  | 500        | 分块大小                                                 |
| KB_CHUNK_OVERLAP               | 50         | 分块重叠                                                 |
| KB_CHUNK_MIN_LENGTH            | 10         | 最短分块长度                                             |
| KB_RETRIEVAL_TOP_N             | 20         | 每路召回条数                                             |
| KB_RRF_K                       | 60         | RRF 融合参数                                             |
| KB_RERANK_THRESHOLD            | 0.35       | 兜底闸门 2 阈值（rerank 分）                             |
| RAG_SCORE_THRESHOLD            | 0.6        | **全局检索阈值**（闸门 1，对齐 step1/01，V1 待接入流程） |
| KB_CACHE_TTL                   | 3600       | 查询缓存 TTL（秒）                                       |
| KB_CACHE_ENABLED               | true       | 查询缓存开关                                             |
| KB_MAX_FILE_SIZE               | 20MB       | 上传大小上限                                             |
| KB_PARSE_TIMEOUT               | 30s        | 解析超时                                                 |
| KB_EMBED_TIMEOUT/RETRIES/BREAK | 5s / 3 / 3 | 对齐 step6/01 通用外部调用参数                           |
| RAG_P95_TARGET                 | 3s         | 检索 P95 时延目标（对齐 step6/02 全局 3s）               |

---

## 9. 独立测试方案（已落地，77 用例全通过）

### 9.1 依赖矩阵

| 依赖                                   | 真实/Mock              | 说明      |
| :------------------------------------- | :--------------------- | :-------- |
| PG 15 / Redis 7 / Milvus 2.4 / ES 8.11 | 真实（docker-compose） | 冒烟验证  |
| embedding-service / rerank-service     | 单元测试 Mock          | HTTP mock |
| 其他 6 个服务                          | 不需要                 | 独立可测  |

### 9.2 功能-断言-测试文件映射（V1 验收基线）

| 能力              | 断言                                                      | 测试文件                                    |
| :---------------- | :-------------------------------------------------------- | :------------------------------------------ |
| 解析器            | TXT/MD；PDF 页眉页脚过滤；DOCX 空段过滤                   | test_documents.py / test_pdf_docx_parser.py |
| 分块              | 段落/句号/硬切；len<10 过滤                               | test_documents.py                           |
| 入库/删除/reindex | 上传成功；增量删除；reindex 旧删新写                      | test_documents.py / test_reindex.py         |
| 混合检索          | 双路召回、RRF 去重、溯源、分类过滤                        | test_search.py                              |
| 双闸门兜底        | 闸门 1：稠密相似度<0.6 无匹配；闸门 2：rerank<0.35 兜底   | test_gate_threshold.py / test_search.py     |
| 分页双支持        | page/page_size 优先、size 兼容回退、默认 20 最大 100      | test_pagination.py                          |
| 缓存兜底隔离      | 兜底/降级响应不写缓存                                     | test_query_cache.py                         |
| 缓存命中落日志    | 命中写 kb_search_logs（cache_hit='1'）                    | test_query_cache.py                         |
| 兜底              | rerank<0.35 → results=[] + 文案                           | test_search.py                              |
| 降级              | Milvus→ES-only / ES→Milvus-only / 双挂→A3005 / Rerank→RRF | test_search_degradation.py                  |
| 缓存              | 写入/命中/TTL/失效/容错                                   | test_query_cache.py                         |
| 限流              | 放行/429+B0006/用户维度/故障降级                          | test_rate_limit.py                          |
| 权限              | B0002 / B0003 / ADMIN+SUPERVISOR 放行                     | test_require_role.py                        |
| 敏感词            | 存在→text/plain 原文 / 缺失→404                           | test_sensitive_words_api.py                 |

### 9.3 冒烟脚本

`scripts/smoke/smoke_rag.sh`：health → 上传 TXT（带 ADMIN token）→ 列表 → 检索 → 删除，5 步全 PASS。

---

## 10. V1 → V2 演进路线

### 10.1 版本能力边界总览

```
V1（本期·已实现）                 V2（多模态+智能检索+规范整改）      V3（可观测+治理）          V4
┌───────────────────┐    ┌────────────────────────┐   ┌──────────────────┐   ┌──────────────┐
│ 4格式解析入库       │    │ 图片OCR / 音频ASR        │   │ LangSmith 追踪    │   │ 对齐 DD-03    │
│ 混合检索+RRF+Rerank │──▶│ Query改写 / 动态路由     │──▶│ 评估模块           │──▶│ V2.0 复杂版    │
│ 兜底/溯源/日志      │    │ 业务加权 / 行级权限      │   │ 版本管理+灰度      │   │ 全部能力       │
│ 缓存/限流/权限      │    │ 多轮对话               │   │ Prompt 动态配置    │   │              │
│ 降级矩阵（5重）     │    │ 敏感词DB表+热刷新        │   │ 敏感词 CRUD       │   │              │
│ 敏感词只读(文件)    │    │ 双闸门0.6接入/同doc去重  │   │                  │   │              │
└───────────────────┘    └────────────────────────┘   └──────────────────┘   └──────────────┘
```

### 10.2 迭代明细

#### V1（本期）— 最小闭环

- **核心目标**：RAG 检索链路可落地、可验收（已达成：65 测试 + smoke 全 PASS）
- **能力**：见 §1.2（13 项）；**必做**：文档入库/删除/reindex、混合检索、Rerank、兜底、溯源、日志、分类隔离、缓存、限流、权限、敏感词只读、降级矩阵；**不做**：见 §1.3
- **技术取舍**：固定 RRF；单轮无会话；PG 日志表替代 LangSmith；ES 落地 BM25（替代内存 pickle 索引）；敏感词文件化；双闸门只实现 rerank 0.35 闸门
- **风险与已控**：Milvus gRPC 代理坑（docker-compose 清代理变量）；langchain 依赖冲突（延后）；Embedding/Rerank 依赖第三方（超时+重试+熔断）

#### V2 — 多模态 + 智能检索 + 规范整改

- **核心目标**：知识库"看得见图、听得见音频、问得准"，并**完成 V1 遗留的规范对齐整改**
- **新增能力**：
  1. 图片 OCR 解析入库（PaddleOCR 独立服务，表格/截图制度文件）
  2. 音频 ASR 转写入库（Provider 抽象，通话录音质检规则）
  3. Query 改写链（LLM：纠错/术语归一/扩展，置信度<0.6 降级原 query）
  4. 动态路由（意图分类规则版 → 权重调整）
  5. 业务加权（kb_documents 增元数据列 customer_id/agent_id/call_date → 新通话×1.5、高危×1.3）
  6. 行级权限（角色/部门过滤 KB 分类，tenant_id 预留）
  7. 多轮对话（Redis session + 追问澄清）
  8. **敏感词整改**：文件 → sensitive_words 表 + `rag:sensitive_words:version` + `kb.sensitive_updated` Pub-Sub（qc 60s 热刷新）
  9. **检索整改**：双闸门 0.6 接入检索流程；同 doc_id 只保留最高分去重
  10. **存储整改**：Milvus 增 version/source 字段（一次性重建索引）；kb_search_logs BOOLEAN→VARCHAR；ES 索引名统一；Redis key 冒号化 + 收录 step4/03
- **技术选型取舍**：OCR/ASR 独立 sidecar 服务（依赖重，不污染主镜像）；Query 改写复用 DashScope（不新增供应商）；意图分类先规则后模型
- **风险点**：PaddleOCR 体积大/CPU 慢 → 独立服务 + 异步入库 + 队列削峰；LLM 改写不确定性 → 置信度降级；索引重建窗口 → 发布窗口内全量 reindex（接受短时不可用）；多轮无状态 → Redis session + 超时清理
- **不做**（推 V3）：LangSmith、评估模块、版本灰度

#### V3 — 可观测与治理

- **核心目标**：RAG 效果可衡量、可回滚、可治理
- **新增能力**：LangSmith 追踪（与 PG 日志并存）；评估模块（召回率/命中率/幻觉率，离线评测集）；文档版本管理+灰度（kb_document_versions 新表，不改原表）；Prompt 动态配置；敏感词 CRUD
- **技术取舍**：LangSmith 与自建日志并存（内部审计 + 效果分析）；版本管理新增表避免 V1 数据迁移
- **风险点**：评估集人工标注成本高；灰度需检索路由版本分流；LangSmith 外部依赖

#### V4 — 对齐复杂版

- **核心目标**：仅当业务确需，补齐 DD-03 复杂版剩余能力（知识图谱等），按需裁剪

### 10.3 版本迁移兼容策略

| 维度         | 策略                                                                                                                                                         |
| :----------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **数据库**   | 只加列/加表，不改既有列语义；status 扩展走 Alembic 增量迁移；kb_search_logs BOOLEAN→VARCHAR 提供默认值，不强制回填；kb_document_versions 新增表              |
| **接口**     | 对外契约（路径/响应包/错误码）向后兼容；新能力新增端点或可选字段（缺省=V1 行为）；分页字段 page/size→page_size 兼容期双字段支持；top_k/category 边界永不收紧 |
| **索引**     | Milvus/ES 只增字段不删字段；version/source 增列在发布窗口一次性重建，且校验 kb_documents.status 与索引一致                                                   |
| **缓存**     | 缓存 key 引入版本段（`rag:cache:query:v2:{md5}`），旧 key 自然过期不迁移                                                                                     |
| **检索行为** | Query 改写/动态路由/业务加权/双闸门 0.6 均以开关引入（KB_QUERY_REWRITE_ENABLED 等），关闭时行为与 V1 完全一致，灰度期可一键回退                              |
| **数据**     | 业务加权/行级权限元数据由 data-platform 推送增量回填，不要求存量一次补齐                                                                                     |
| **敏感词**   | V2 切换 DB 表数据源时，以 seed 迁移同步 .dict 文件内容 → sensitive_words 表，保持 50 词基线不漂移                                                            |

---

## 11. 开发任务拆分

### 11.1 任务卡说明

优先级：P0=链路阻断/验收必过；P1=生产必需非阻断；P2=增强项。依赖：前置任务。交付物：独立可验收产出。版本归属：V1 必做（已全部完成 ✅）/ V2 / V3。

### 11.2 V1 任务清单（已完成基线 + 2 项整改待办）

| #      | 任务                                                                     | 优先级 | 依赖     | 交付物                                                    | 状态 |
| :----- | :----------------------------------------------------------------------- | :----- | :------- | :-------------------------------------------------------- | :--- |
| V1-T1  | 数据库迁移：status CHECK 扩展 + kb_search_logs                           | P0     | —        | 迁移 0002                                                 | ✅   |
| V1-T2  | 解析器：base/text/md/pdf/docx + Factory                                  | P0     | —        | app/parsers/\*                                            | ✅   |
| V1-T3  | 数据访问：doc_repo + chunk_repo                                          | P0     | T1       | app/repositories/\*                                       | ✅   |
| V1-T4  | 向量存储：milvus/es/embed（超时/重试/熔断）                              | P0     | —        | app/vectorstore/\*                                        | ✅   |
| V1-T5  | chunk_service + ingest_service（多存储回滚）                             | P0     | T2/T3/T4 | app/services/\*                                           | ✅   |
| V1-T6  | search_service + log_service（RRF/兜底/降级）                            | P0     | T4/T5    | app/services/\*                                           | ✅   |
| V1-T7  | API + models                                                             | P0     | T5/T6    | app/api/_ + app/models/_                                  | ✅   |
| V1-T8  | main.py 扩展                                                             | P0     | T7       | app/main.py                                               | ✅   |
| V1-T9  | 单元测试（解析/入库/检索/兜底/分类/trace_id）                            | P1     | T7       | test_documents/test_search                                | ✅   |
| V1-T10 | 冒烟脚本 + 端到端                                                        | P0     | T8       | smoke_rag.sh                                              | ✅   |
| V1-T11 | Redis 查询缓存                                                           | P1     | T6       | query_cache.py + 测试                                     | ✅   |
| V1-T12 | 敏感词只读接口（文件源）                                                 | P1     | T7       | sensitive_words.py + .dict + 测试                         | ✅   |
| V1-T13 | 接口限流                                                                 | P1     | T11      | rate_limit.py + 测试                                      | ✅   |
| V1-T14 | 权限校验                                                                 | P1     | T13      | require_role.py + 测试                                    | ✅   |
| V1-T15 | 降级/reindex/PDF-DOCX 测试                                               | P1     | T9       | test_search_degradation/test_reindex/test_pdf_docx_parser | ✅   |
| V1-T16 | openspec 契约同步                                                        | P1     | T11~15   | openspec/changes/rag-service-v1/specs/\*                  | ✅   |
| V1-T17 | 整改：双闸门 0.6 接入检索流程（对齐 step1/01）                           | P1     | T6       | search_service 增融合分<0.6 无匹配判定 + 测试             | ✅   |
| V1-T18 | 整改：分页字段 page/size→page_size 双支持（对齐 step5/02）               | P2     | T7       | models/document.py 兼容字段 + 测试                        | ✅   |
| V1-T19 | 整改：缓存切面跳过兜底/降级响应（is_fallback 或 degraded=true 不写缓存） | P1     | T11      | query_cache.py is_cacheable 判定 + 测试                   | ✅   |
| V1-T20 | 整改：缓存命中落 kb_search_logs（cache_hit='1'，保障命中率监控不失真）   | P2     | T19      | 迁移 0003 + log_service/api/search 改造 + 测试            | ✅   |

**V1 验收标准**：77 测试全过 + smoke 5 步 PASS + ruff 零新增问题 + 限流/权限/缓存实测符合契约 + 双闸门 0.6 接入、分页 page_size、缓存命中落日志（实测 cache_hit='1'）、兜底/降级不写缓存四项整改完成。

### 11.3 V2 任务清单（迭代规划）

| #      | 任务                                                                                                            | 优先级 | 依赖      | 交付物                        |
| :----- | :-------------------------------------------------------------------------------------------------------------- | :----- | :-------- | :---------------------------- |
| V2-T1  | PaddleOCR 服务化（独立容器 + /ocr + 批量异步入库）                                                              | P0     | —         | deploy/ocr/                   |
| V2-T2  | 图片解析入库链路（.png/.jpg→OCR→分块→双索引）                                                                   | P0     | V2-T1     | 解析器扩展 + 入库             |
| V2-T3  | 音频 ASR 转写接入（Provider 抽象）                                                                              | P1     | —         | ASR 服务 + 入库               |
| V2-T4  | Query 改写链（LLM + 置信度降级）                                                                                | P1     | V1-T6     | query_rewrite_service.py      |
| V2-T5  | 动态路由（意图规则版 + 权重配置）                                                                               | P1     | V2-T4     | routing_service.py            |
| V2-T6  | 业务加权（元数据列 + scoring 权重）                                                                             | P2     | V2-T5     | 迁移 0003 + 加权              |
| V2-T7  | 行级权限（分类角色矩阵 + tenant_id 预留）                                                                       | P2     | V2-T6     | require_role 扩展             |
| V2-T8  | 多轮对话（Redis session + 追问）                                                                                | P2     | V1-T11    | session_service.py            |
| V2-T9  | 敏感词整改：DB 表 + 版本号 + `kb.sensitive_updated` Pub-Sub                                                     | P0     | V1-T12    | 迁移 + publish + 订阅对齐测试 |
| V2-T10 | 检索整改：同 doc_id 最高分去重（对齐 step4/02）                                                                 | P1     | V1-T6     | search_service 收敛 + 测试    |
| V2-T11 | 存储整改：Milvus 增 version/source（重建索引）、ES 索引名统一、kb_search_logs BOOLEAN→VARCHAR、Redis key 冒号化 | P1     | V2-T9/T10 | 迁移 0004 + 重建脚本          |

**V2 验收标准**：OCR 图片可检索命中；Query 改写开关关闭时行为与 V1 一致（可回退）；敏感词热刷新 60s 内 qc 侧生效；整改项全量回归 65+ 用例通过。

### 11.4 V3 任务清单（规划，不排期）

| #     | 任务                                             | 优先级 | 依赖  |
| :---- | :----------------------------------------------- | :----- | :---- |
| V3-T1 | LangSmith 追踪接入（与 PG 日志并存）             | P1     | —     |
| V3-T2 | 评估模块（离线评测集 + 召回率/幻觉率）           | P0     | V3-T1 |
| V3-T3 | 文档版本管理 + 灰度（kb_document_versions 新表） | P1     | V3-T2 |
| V3-T4 | Prompt 模板动态配置                              | P2     | —     |
| V3-T5 | 敏感词 CRUD（只读升级全量管理）                  | P2     | V2-T9 |

---

## 12. 落地现状对照（2026-08-21）

- rag-service-v1 变更：47/47 任务完成，openspec validate 通过
- 全量测试：**77 passed**（含 V1-T17~T20 新增 12 用例）；冒烟：smoke_rag.sh 全链路 PASS
- 实测：限流 61 次→429+B0006；权限 401/403/放行三态；缓存 2.06s→0.003s；删除后缓存失效正确；**双闸门 0.6 已接入（闸门 1 稠密相似度<0.6 判定无匹配）**；**分页 page_size 双支持实测通过**；**缓存命中落日志实测 cache_hit='1'（1ms）vs 真实检索 cache_hit='0'（2817ms）**；**兜底/降级响应不写缓存**
- **V1 整改待办已清零**；剩余规范对齐项均为 V2（敏感词 DB 表化、Milvus/ES schema、kb_search_logs BOOLEAN→VARCHAR、Redis key 收录）
- 非 V1 阻断：JWT_SECRET 加载路径统一（服务启动从项目根读 .env）

---

## 附录 A：与全局规范差异清单（A1~A14）

> 依据：step1/01、step3/03、step4/01~04、step5/02、step6/01~02。每项含【规范契约 / V1 实现 / 处置】；处置统一收敛到 §11 任务清单，禁止子系统私自改规范。

| #   | 主题                           | 规范契约（权威文件）                                                                                      | V1 实现现状                                                                                                      | 处置                                                         |
| :-- | :----------------------------- | :-------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------- |
| A1  | 双闸门阈值体系                 | 检索 score<0.6 判定无匹配（step1/01、step3/03、step4/02 §四.1）                                           | 仅实现 rerank<0.35 兜底；RAG_SCORE_THRESHOLD=0.6 未接入流程                                                      | **V1-T17 ✅**（稠密相似度<0.6 判定无匹配，ES-only 降级跳过） |
| A2  | 敏感词数据源                   | sensitive_words 表 + `rag:sensitive_words:version` + `kb.sensitive_updated` Pub-Sub（step4/01、step4/03） | 返回内置 .dict 文件（50 词），无版本号/Pub-Sub                                                                   | **V2-T9**                                                    |
| A3  | 兜底/降级响应缓存              | 检索命中率监控依赖 kb_search_logs 真实性（step6/02 §5.2）                                                 | api/search.py 无条件写缓存：兜底/降级结果缓存 1h；缓存命中不落日志 → 监控失真                                    | **V1-T19/T20 ✅**（兜底/降级不写缓存；命中落 cache_hit='1'） |
| A4  | 检索去重                       | 同 doc_id 多分块只保留最高分（step4/02 §四.2）                                                            | 仅按 chunk_id 去重                                                                                               | **V2-T10**                                                   |
| A5  | Milvus schema                  | 6 字段含 version(INT64)/source(VARCHAR255)（step4/02 §二）                                                | 以 chunk_id_str/doc_id_str 冗余替代，无 version/source                                                           | **V2-T11**                                                   |
| A6  | ES 索引名                      | kb_chunks_bm25（step4/02 §七）                                                                            | 实现为 kb_bm25                                                                                                   | **V2-T11**                                                   |
| A7  | 表类型规范                     | 枚举/布尔一律 VARCHAR+CHECK，禁止 BOOLEAN（step4/01 §1.2）                                                | kb_search_logs.degraded/is_fallback 用 BOOLEAN                                                                   | **V2-T11**                                                   |
| A8  | 分页字段                       | page/page_size（默认 20 最大 100）（step5/02）                                                            | 列表接口实现字段为 size                                                                                          | **V1-T18 ✅**（page_size 优先、size 兼容回退，实测通过）     |
| A9  | Redis key 收录                 | step4/03 key 清单仅 3 项                                                                                  | V1 新增 `rag:cache:query:*`/`rag:cache:keys:*`/`rag:ratelimit:*`/`rag:embed:degraded`/`rag:rerank:degraded` 5 类 | 待 step4/03 收录                                             |
| A10 | Redis key 命名（规范自身冲突） | step4/03 要求冒号分隔 `<prefix>:<entity>:<id>`                                                            | 熔断标识用下划线（同 step6/01 `{service}:llm_degraded` 风格）                                                    | 规范层统一，V2 随 T11 调整                                   |
| A11 | Embedding 失败处理             | "失败→跳过向量化+告警"降级入库（step6/01 §6.1）                                                           | 失败置 status=failed + A3003（防无向量脏数据）                                                                   | §6.1 已标注；V2 可选支持降级入库                             |
| A12 | kb_documents.status            | CHECK ('active','archived')（step4/01）                                                                   | 扩展 ('processing','active','archived','failed')                                                                 | 合理演进，待 step4/01 同步收录                               |
| A13 | 数据中台对接                   | 检索日志入 dw（etl_task_logs/llm_call_logs）（step4/02 §四.4）                                            | 仅本地 kb_search_logs 落库，未接订阅/ETL 管道                                                                    | V2 数据管道接通（被动订阅）                                  |
| A14 | Redis 规范自身 DB 编号         | step4/03 §3.1/3.2 小标题 DB 编号差一位（DB1/DB2 互换）                                                    | —（不影响实现，DB2 已按正文生效）                                                                                | 规范层修正，与本服务无代码改动                               |

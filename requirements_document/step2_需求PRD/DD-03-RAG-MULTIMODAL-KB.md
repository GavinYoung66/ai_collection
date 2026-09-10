# 子系统详细设计 #03：RAG 知识库服务（简化版 V1，规范对齐版 V1.2）

> 子系统编号：第 3 号
> 服务名：`rag-service`
> 监听端口：**8002**
> 里程碑归属：**M1（最小闭环 RAG 流程跑通；M1 验收仅 Milvus 稠密检索，见 step1/04「检索边界」）**
> 上游约束：step1/01~04 全局口径（含检索边界）、step3/01~03 架构与降级、step4/01~04 数据设计、step5/01~04 技术规范、step6/01~02 异常容错与可观测性
> 复杂版参考：DD-03-RAG-MULTIMODAL-KB.md（V2.0，含多模态/复杂路由/Query改写/LangSmith，本期不实现；**不得作为里程碑 M1 开工依据**）
> 本文档状态：**与 rag-service V1~V4 已实现代码对齐**（缓存/限流/权限/敏感词/降级/多模态 OCR-ASR/摘要均已落地并通过全量测试）；**V5 为本期规划**（新增 Excel/PPT 格式、内嵌图片/表格抽取、AMR/WMA 音频、Gradio 浏览器前端、在线大模型回答开关；**不得作为里程碑 M1 开工依据**）

---

## 0. 文档修订记录

| 版本 | 日期       | 修订人        | 修订内容                                                                                                                                                                                                                                                                                |
| :--- | :--------- | :------------ | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| V1.0 | 2026-08-20 | 规范优化      | 对齐项目实际技术栈重写：PG+Milvus+ES+Redis；补充完整目录结构、业务流程、函数签名；明确 V1 范围与延后清单                                                                                                                                                                                |
| V1.1 | 2026-08-21 | AI 产品架构师 | 评审优化：删除代码骨架冗余；补齐边界契约；修正敏感词契约（text/plain）；深化 §10 V1→V2 演进与 §11 任务拆分                                                                                                                                                                              |
| V1.2 | 2026-08-21 | AI 产品架构师 | **全局规范对齐版**：逐条核对 step1/03/04/05/06 规范，修正 B0001 状态码、双阈值闸门（0.6/0.35）、Milvus/ES schema 差异、敏感词数据源差异、Redis key 收录状态、kb_search_logs 类型规范冲突、数据中台对接与监控；全部差异以"规范契约 / V1 实现 / 差异与演进"三段式标注                     |
| V1.3 | 2026-08-25 | AI 产品架构师 | **V5 需求合并版**：新增 V5 多模态混合检索增强（Excel/PPT 格式、PDF/PPT/Word 内嵌图片与表格抽取、AMR/WMA 音频扩展）、Gradio 浏览器前端入口、RAG 检索接入在线大模型（开关门控）；同步融入 §1.4 边界、§2 技术栈、§3 目录、§4 存储、§5 接口、§6 流程、§8 配置、§10 演进路线、§11.5 任务清单 |
| V1.4 | 2026-09-10 | Ellis Guo | 数据模型收口：kb_documents.status 四值与 kb_search_logs 已收录 step4/01；degraded/is_fallback/cache_hit 为 CHAR(1) 禁止 BOOLEAN；A7/A12 关闭 |
| V1.5 | 2026-09-10 | Ellis Guo | 删除 A12 旧行；V2 存储整改不再把 kb_search_logs BOOLEAN 当待办（类型已收口 step4/01） |
| V1.6 | 2026-09-10 | Ellis Guo | 审阅 17/18：M1 验收仅 Milvus；ES+RRF+Rerank 标非一期强制；Embedding 失败与 step6/01 对齐为 failed+A3003 |
| V1.7 | 2026-09-10 | Ellis Guo | 审阅 32/35/36：探活仅 GET /health；A3004/A3006 与 step6 一致禁止作 HTTP code；分页仅 page_size、立即废弃 size |
| V1.8 | 2026-09-10 | Ellis Guo | 审阅 40：词库热刷新 60s 句加「stale 时钟见 step6/02」（不改 V2-T9 实现范围） |
| V1.9 | 2026-09-10 | Ellis Guo | 审阅 48：文首加强 V2.0/V5 **不得作为里程碑 M1 开工依据** |

### 0.1 规范对齐声明（单一事实源）

| 主题                            | 权威文件 | 本 PRD 引用要点                                                                      |
| :------------------------------ | :------- | :----------------------------------------------------------------------------------- |
| 错误码全集（A 58 + B 10 = 共 68 条编码位） | step6/01 | A3xxx 知识库域 + B0001~B0010；禁止私自编号                                           |
| 统一响应包 / HTTP 状态码 / 分页 | step5/02 | `{code,message,data,trace_id}`；业务错误 200、系统错误 5xx、参数 422；仅 `page`/`page_size`，禁止 `size` 别名 |
| 向量库/ES/Rerank Schema         | step4/02 | kb_vectors 6 字段、kb_chunks_bm25、bge-reranker 约束；**一期仅 Milvus**，ES/rerank 为二期预案 |
| 一期检索边界                    | step1/01、step1/04 | M1 验收只要求 Milvus 稠密检索；ES BM25 / RRF / rerank 开关默认关，不强制部署 |
| Redis key / DB 分配 / Pub-Sub   | step4/03 | rag 前缀 DB2、键命名冒号分隔、`kb.sensitive_updated`                                 |
| KB 表 DDL / 类型规范            | step4/01 | kb_documents（status 四值）/ kb_chunks / kb_search_logs / sensitive_words；枚举用 VARCHAR+CHECK；禁止 BOOLEAN |
| 降级与兜底分级                  | step3/03 | L1 LLM / L2 检索无果(score<0.6) / L3 合规；兜底话术入 script 标准答案库              |
| 重试/超时/熔断                  | step6/01 | 5s 超时、3 次指数退避（1s/2s/4s）、3 连失败熔断                                      |
| 埋点/监控/告警                  | step6/02 | rag 监控项：检索命中率<60%、Embedding 队列积压>100、版本切换失败>0                   |

---

## 1. 模块概览

### 1.1 定位

面向电话催收业务的**轻量企业级 RAG 后端服务**。坐席输入业务问题，系统从私有合规文档中检索相关知识片段，返回带溯源引用的检索结果（不生成话术，由 call-service 消费片段）。**V5 起**：新增 Gradio 浏览器前端入口（上传/问答可视化），并支持经开关接入在线大模型生成带溯源的回答（默认关闭，关闭时行为与 V4 完全一致）。

### 1.2 V1 能力边界（已实现，验收基线）

> **一期/M1 验收边界（对齐 step1/04「检索边界」、step4/02）**：M1 只要求 **Milvus 稠密检索** 可跑通。下表中 ES BM25、RRF、Rerank、ES 双索引、ES-only 降级路径为 **V1 代码可含开关的预案**，覆盖矩阵一期默认关；**M1 不强制部署 ES / rerank-service**。禁止把混合检索当作 M1 部署条件。

| #   | 能力                | 说明                                                                       | 状态 |
| :-- | :------------------ | :------------------------------------------------------------------------- | :--- |
| 1   | 文档上传 + 解析入库 | PDF/DOCX/MD/TXT，解析→清洗→分块→向量化→Milvus 索引。ES 双写为 **二期/可选**（开关默认关；代码可保留 client） | ✅   |
| 2   | 增量更新/删除       | 单文档删除仅操作该文档的向量/索引/chunks，禁止全量重建                     | ✅   |
| 3   | 检索（M1 必做）     | **M1 验收：仅 Milvus 稠密检索**。Milvus + ES BM25 → RRF(k=60) → Rerank 为 **非一期强制**（二期/开关默认关） | ✅   |
| 4   | Reranker 重排序     | rerank-service:8101；宕机降级 RRF score 排序。**非一期强制**；M1 不部署 rerank-service | ✅（预案） |
| 5   | 低分兜底（防幻觉）  | M1：Milvus 召回归一化 score < 0.6 → 兜底。闸门 2（rerank_score < 0.35）属二期 rerank 路径 | ✅   |
| 6   | 溯源引用            | 结果附 doc_id/title/chunk_index/content/score（二期可附 rerank_score）     | ✅   |
| 7   | 全链路日志          | 每次检索落库 kb_search_logs（15 字段）                                     | ✅   |
| 8   | 分类隔离            | category ∈ {regulation, script, case, other} 过滤                          | ✅   |
| 9   | Redis 高频查询缓存  | `rag:cache:query:{md5}` + 按分类失效 + 容错降级                            | ✅   |
| 10  | 接口限流            | Redis 按用户维度固定窗口，超限 429+B0006                                   | ✅   |
| 11  | 接口权限校验        | 上传/删除/reindex 需 ADMIN/SUPERVISOR，401+B0002 / 403+B0003               | ✅   |
| 12  | 敏感词只读接口      | GET /sensitive-words/current.dict（INTERNAL，text/plain）                  | ✅   |
| 13  | 降级/熔断           | M1：Milvus 宕机 → A3005（无 ES 兜底义务）。ES-only / Rerank→RRF / 双挂降级矩阵为 **非一期强制** 预案。Embedding 熔断见 §6.4 | ✅   |

### 1.3 V1 明确不做（延后清单见 §10）

多模态解析（ASR/OCR）、Query 改写、动态路由、业务加权、行级权限、多轮对话、版本管理+灰度、LangSmith、评估模块、Prompt 动态配置、敏感词 CRUD。延后原因与版本规划统一见 §10。

### 1.4 AI 边界约束

- rag-service **只提供被动检索**，不主动向外调用服务（不调 case-service / data-platform）
- 不修改案件状态（归 case-service）；不做质检打分（归 qc-service）
- **不生成话术（V5 起开关门控）**：V1~V4 返回检索片段 + 溯源，不接 LLM（`include_answer` 已移除）；**V5 新增 `KB_LLM_ANSWER_ENABLED`（默认 false）**：开启时检索后接入在线大模型生成回答（带溯源引用，供前端展示），关闭时行为与 V4 完全一致；LLM Provider 抽象支持在线/本地切换，LLM 调用超时/熔断/不可用一律降级为纯检索结果（不抛错）
- 接口调用顺序：**先权限校验 → 再限流 → 再执行业务逻辑**

---

## 2. 技术栈（已落地，对齐 step1/01、step4/02）

| 组件           | 选型                                                                      | 说明                                                                                                                                    |
| :------------- | :------------------------------------------------------------------------ | :-------------------------------------------------------------------------------------------------------------------------------------- |
| Web 框架       | FastAPI + Uvicorn                                                         | 已有骨架                                                                                                                                |
| 业务数据库     | PostgreSQL 15                                                             | kb_documents / kb_chunks / kb_search_logs / sensitive_words                                                                             |
| 向量数据库     | Milvus 2.4.13                                                             | collection `kb_vectors`（BGE-M3 1024dim，IVF_FLAT nlist=1024，metric=IP，nprobe=16）                                                    |
| 关键词检索     | Elasticsearch 8.11.3                                                      | index `kb_bm25`（ik_max_word；规范名为 kb_chunks_bm25，差异见 §4.3）                                                                    |
| 缓存/限流/熔断 | Redis 7（DB 2，前缀 `rag:`）                                              | 查询缓存 + 限流计数 + 熔断标识                                                                                                          |
| Embedding      | embedding-service:8100                                                    | BGE-M3 1024dim，5s 超时/3 次退避/3 连失败熔断                                                                                           |
| Reranker       | rerank-service:8101                                                       | bge-reranker-large，score∈[0,1]                                                                                                         |
| LLM            | **V5 开关门控接入**                                                       | `KB_LLM_ANSWER_ENABLED=false` 默认不接入；开启后经 LLM Provider 抽象（在线 DashScope / 本地 OpenAI 兼容）生成带溯源回答，失败降级纯检索 |
| Gradio 前端    | Gradio（独立进程）                                                        | V5 浏览器入口：上传页 + 问答页，全链路可视化（问题/召回 chunk/图片表格预览/LLM 回答）                                                   |
| 文档解析       | pdfplumber / python-docx / **openpyxl / python-pptx** / **PyMuPDF(fitz)** | V5 新增 .xlsx/.pptx 解析；PDF/DOCX/PPTX 内嵌图片抽取（含矢量图渲染位图）与内嵌表格结构化解析                                            |
| 音频解码       | ASR Provider + **ffmpeg/pydub**                                           | V5 新增 AMR/WMA 电话录音格式解码，兼容既有 MP3/WAV/M4A                                                                                  |
| 追踪           | X-Trace-Id                                                                | common/trace/middleware.py，`trace_<ulid>`                                                                                              |
| 错误码         | A3001~A3006 + B0001~B0006                                                 | 对齐 step6/01，不新增                                                                                                                   |

**健康检查依赖矩阵**（对齐 step1/01）：`GET /health` 依赖 PostgreSQL、Milvus、Redis、embedding-service 四者探活。

---

## 3. 目录结构与文件职责

```
services/rag-service/
├── app/                          # 应用主目录（六层架构：api → models → parsers → repositories → services → vectorstore）
│   ├── main.py                   # FastAPI 入口：路由注册 + 鉴权/限流/异常 handler + startup（Milvus/ES 初始化 + 敏感词基线 + 补索引恢复）+ shutdown 幂等关闭全局客户端 + /health 根探活
│   ├── api/                      # 路由层（Controller）：先权限 → 再限流 → 再业务
│   │   ├── health.py             # GET /health：依赖探活（PG/Milvus/Redis/embedding）。**仅 `/health` 为探活契约**；实现层若残留 `/api/v1/health` 不算接口规范，不得写成正式契约
│   │   ├── deps.py               # 依赖注入（db/redis/各客户端）+ success_response 统一响应包 + save_upload_file（≤20MB 校验 + 临时文件清理）
│   │   ├── documents.py          # 上传/列表/详情/删除/reindex（权限 + 限流 + 缓存失效）
│   │   ├── search.py             # 混合检索（限流 200/min + 缓存切面 + 请求级超时）
│   │   └── sensitive_words.py    # GET /sensitive-words/current.dict（INTERNAL，text/plain 原样返回）
│   ├── models/                   # Pydantic 模型层
│   │   ├── orm.py                # SQLAlchemy ORM：kb_documents（含 summary）/ kb_chunks / kb_search_logs / sensitive_words
│   │   ├── document.py           # DocumentUploadResponse / DocumentListItem / DocumentListResponse
│   │   └── search.py             # SearchRequest / SourceItem / SearchResponse
│   ├── parsers/                  # 文档解析层（ABC + ParserFactory 按扩展名分发）
│   │   ├── base.py               # DocumentParser ABC：async extract_text()
│   │   ├── text_parser.py        # TXT：UTF-8 + 编码检测 fallback
│   │   ├── md_parser.py          # MD：正则去除 #/`/*/-
│   │   ├── pdf_parser.py         # PDF：pdfplumber，页文本>4 行去首尾各 2 行（页眉页脚）
│   │   ├── docx_parser.py        # DOCX：python-docx 段落提取，空段过滤
│   │   ├── image_parser.py       # 图片→OCR（PaddleOCR sidecar ocr-service:8200）
│   │   ├── audio_parser.py       # 音视频→ASR（Provider 抽象：DashScope/Whisper；VAD 静音裁剪 + 60s 分片 + 开场白裁剪）
│   │   ├── xlsx_parser.py        # Excel：全部 sheet，首行=表头，行切块（列名:值 序列化），sheet 名前缀（V5）
│   │   ├── pptx_parser.py        # PPT：每页 标题+正文+演讲者备注（备注并入页文本），过滤页码/版式占位文字（V5）
│   │   └── asset_extractor.py    # PDF/DOCX/PPTX 内嵌图片抽取（PNG/JPEG/位图/矢量图渲染）+ 内嵌表格结构解析（V5）
│   ├── repositories/             # 数据访问层（Repository）
│   │   ├── doc_repo.py           # kb_documents：create/get/list（分组聚合计数，无 N+1）/update_status/soft_delete/summary 读写
│   │   └── chunk_repo.py         # kb_chunks：batch_create/delete_by_doc/get_by_ids
│   ├── services/                 # 业务逻辑层（Service）
│   │   ├── chunk_service.py      # 分块：段落→句号→chunk_size，overlap=50，过滤 len<10
│   │   ├── ingest_service.py     # 入库编排（§6.1）；解析超时（KB_PARSE_TIMEOUT）走既有失败路径；ES 降级补索引持久化（rag:backfill:pending）；删除外部索引失败重试 2 次
│   │   ├── search_service.py     # 检索编排：双路并行→RRF→同 doc_id 去重→业务加权→Rerank→行级权限→双闸门兜底；降级 §6.4；非法 UUID 召回跳过
│   │   ├── log_service.py        # kb_search_logs 落库（写入失败不阻断主流程）
│   │   ├── query_cache.py        # 查询缓存：key=md5(query+category+top_k)，分类 Set 索引，禁 KEYS
│   │   ├── rate_limit.py         # 限流：Redis INCR+EXPIRE，X-User-Id 优先/IP 回退，429+B0006
│   │   ├── require_role.py       # 权限：Bearer JWT（common.security.rbac），401+B0002 / 403+B0003
│   │   ├── asr_client.py         # ASR 客户端（熔断 rag:asr:degraded，Redis 故障按未熔断处理）
│   │   ├── asr_providers/        # ASR Provider 抽象：base/factory/dashscope_provider/whisper_provider（并发加锁懒加载）
│   │   ├── ocr_client.py         # OCR 客户端（ocr-service:8200）
│   │   ├── summary_service.py    # V4 摘要：ES doc_summary → PG summary 写入顺序（ES→PG 防脏状态）
│   │   ├── llm_service.py        # V5 LLM 回答：Provider 抽象（在线 DashScope / 本地 OpenAI 兼容）+ KB_LLM_ANSWER_ENABLED 门控 + 超时/熔断降级
│   │   ├── sensitive_words_sync.py # 敏感词版本号 + Pub-Sub 热刷新（kb.sensitive_updated）
│   │   ├── query_rewrite_service.py # V2 Query 改写（KB_QUERY_REWRITE_ENABLED）
│   │   ├── routing_service.py    # V2 动态路由（KB_ROUTING_ENABLED）
│   │   ├── weighting_service.py  # V2 业务加权（KB_WEIGHTING_ENABLED）
│   │   ├── row_access_service.py # V2/V3 行级权限（KB_ROW_LEVEL_ENABLED，fail-closed）
│   │   └── session_service.py    # V2 多轮会话（KB_MULTITURN_ENABLED）
│   └── vectorstore/              # 向量存储客户端层
│       ├── milvus_client.py      # kb_vectors：UUID→INT64 + str 冗余字段，insert/search/delete_by_doc/close
│       ├── es_client.py          # kb_bm25：ik_max_word（缺插件回退 standard），index/search/delete_by_doc；V4 doc_summary 检索副本
│       ├── embed_client.py       # embedding-service:8100：超时/重试/熔断（rag:embed:degraded TTL 60s）
│       └── rerank_client.py      # rerank-service:8101：精排，失败降级 RRF score 排序
├── data/
│   └── sensitive_words.dict      # 敏感词字典（每行一词，与 seed 一致；V1 只读接口数据源，差异见 §5.3）
├── tests/                        # 40 个测试文件（全量基线 240 passed / 1 skipped，V1→V4 分代归档）
│   ├── conftest.py               # 共享 fixture（TestClient / DB / Redis mock）
│   ├── V1 基线：test_health / test_documents / test_pdf_docx_parser / test_search / test_search_degradation /
│   │           test_gate_threshold / test_query_cache / test_rate_limit / test_require_role /
│   │           test_sensitive_words_api / test_pagination / test_reindex
│   ├── V2：test_embed_es_only_degradation / test_ingest_degradation / test_routing / test_query_rewrite /
│   │       test_weighting / test_row_access / test_session / test_logs_v2 / test_search_dedup_docid /
│   │       test_sensitive_words_sync / test_sensitive_seed_publish / test_redis_fault_tolerance /
│   │       test_backward_compat / test_0004_seed_consistency
│   ├── V3：test_multimodal_parsers / test_asr_vad / test_asr_opening_trim / test_row_access_fail_closed
│   └── V4/fix：test_summary_metadata / test_api_health / test_api_upload / test_api_deps / test_ingest_service /
│               test_whisper_provider / test_asr_client / test_doc_repo / test_search_service / test_main
├── gradio/                       # V5 Gradio 浏览器前端：上传页 + 问答页 + 图片/表格预览 + 异常友好提示
└── Dockerfile                    # 镜像：公共基础镜像 + 全局 requirements 安装 + conf/ + 功能开关 ENV（V2 起）
```

---

## 4. 数据库与存储

### 4.1 PostgreSQL（DDL 单一事实源为 step4/01，本表为差异对照）

- **kb_documents**（status 已扩展）：
  - ▶ **规范契约**（step4/01）：status CHECK `('processing','active','archived','failed')`，category VARCHAR(50) + CHECK 四值
  - ▶ **V1 实现**：与规范一致
  - ▶ **差异与演进**：**已同步 step4/01**（A12 关闭）
- **kb_chunks**：id/doc_id(FK CASCADE)/chunk_index/content/vector_id(VARCHAR100)/category/version/公共字段；`UNIQUE(doc_id, chunk_index)` + 唯一索引 vector_id
- **kb_search_logs**（已收录 step4/01）：
  - ▶ **规范契约**（step4/01）：trace_id/query/category/dense_hit_count/sparse_hit_count/fused_count/reranked_count/max_rerank_score/hit_doc_ids/hit_chunk_ids/latency_ms/degraded/is_fallback/cache_hit/create_time/is_delete；布尔语义 CHAR(1) `'0'|'1'`
  - ▶ **V1 实现**：与 step4/01 对齐（禁止 BOOLEAN）
  - ▶ **差异与演进**：**已同步 step4/01**（A7 关闭）
- **sensitive_words**（规范表）：word(VARCHAR100 UNIQUE)/category(threat/abuse/induce)/severity(high/medium/low)/status(active/disabled)/description —— V1 只读接口数据源为文件而非本表，见 §5.3
- **kb_doc_assets**（V5 新增表）：id / doc_id(FK CASCADE) / asset_type(image|table) / page(INT) / storage_path / ocr_text / mime_type / created_at —— PDF/DOCX/PPTX 内嵌图片/表格抽取的元数据与存储定位：图片文件落本地磁盘/对象存储（供预览），OCR 文本以 chunk 走既有双索引（文搜图），检索命中 chunk 时按 doc_id+asset 关联返回预览元数据

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
**列表分页**：`page`（从 1 起）、`page_size`（默认 20，最大 100）——▶ 对齐 step5/02。**仅 `page_size`，禁止 `size` 别名**（立即废弃，无兼容回退）。

**边界契约**：

- 格式白名单：`.pdf/.docx/.md/.txt`（V1）+ `.png/.jpg/.jpeg`（V2 OCR）+ `.mp3/.wav/.m4a`（V2 ASR）+ `.xlsx/.pptx`（V5 表格/演示）+ `.amr/.wma`（V5 电话录音）；其他（含 .exe）→ A3001；空文件/损坏加密 PDF → A3001，status=failed
- 同名策略：每次上传生成新 doc_id，不覆盖
- 删除不存在/已 archived 的 doc_id → B0004（非幂等：重复删除返回资源不存在）
- reindex 对 failed/archived 文档 → B0004（状态机约束）

### 5.2 混合检索（**非一期强制**；M1 默认仅 Milvus）

> 本节保留混合检索设计，供二期/开关开启后使用。覆盖矩阵一期默认关；**M1 验收只要求** `embed(query) → Milvus 稠密检索 → score<0.6 兜底`。不得将 ES + RRF + Rerank 当作 M1 必部署。

| 方法 | 路径         | 权限                | 限流    | 说明     |
| :--- | :----------- | :------------------ | :------ | :------- |
| POST | `/kb/search` | ALL（跳过权限校验） | 200/min | 混合检索 |

**请求体**：`{query: str, category?: str, top_k: int=5, answer?: bool}`；**入参边界**：top_k∈[1,20]、category 四值、query 非空（非法→B0001，HTTP 422）；`answer`（V5）缺省 false，仅当 `KB_LLM_ANSWER_ENABLED=true` 且请求显式开启时生成 LLM 回答
**响应体**：`{results:[{doc_id,title,chunk_index,content,score,rerank_score,summary?,assets?}], answer?, degraded, query_original, message, trace_id}`——`answer`（V5，LLM 开关开启时生成，含溯源引用）、`results[].assets`（V5，命中 chunk 关联的内嵌图片/表格预览元数据，缺省为空）

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

- ▶ **规范契约**（step4/01 + step4/03）：敏感词存 sensitive_words 表（word/category/severity/status），版本号 `rag:sensitive_words:version` 自增，更新后 PUBLISH `kb.sensitive_updated`，qc-service 60s 内拉取重建 AC 自动机（stale 时钟见 step6/02）
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

**V5 扩展**：新增 .xlsx/.pptx 解析走同一七步流水线（source 打标 excel/ppt）；PDF/DOCX/PPTX 内嵌图片经抽取 → OCR 文本以 chunk 入库（source=asset_image）+ 元数据落 kb_doc_assets（文搜图）；内嵌表格结构化解析（保留行列结构）后入库。**回滚契约与 V1 完全一致**（PG 成功但索引失败 → 回滚 + status=failed + A3003）。

- ▶ **与 step6/01 §6.1 对齐（审阅 18）**：Embedding/向量化失败 → `kb_documents.status=failed` + 对外/对内 **A3003**，告警可保留。**禁止**以「跳过向量化、文档仍 active/可检索」收场。检索不得命中无向量文档。

### 6.2 增量删除

```
DELETE /documents/{doc_id} → 权限/限流 → 删 PG chunks → 删 Milvus vectors
→ 删 ES docs → status=archived → invalidate_cache(category)
```

### 6.3 混合检索（**非一期强制**）

> M1 简化链路：`POST /search → 限流 → embed(query) → Milvus 稠密召回 → score<0.6 兜底`。下列并行召回 + RRF + Rerank 为二期/开关开启后的设计，**不是 M1 部署条件**。

```
POST /search → 限流 → embed(query) → 并行召回(Milvus+ES, 各 top20, category 过滤)
→ RRF(k=60) 融合去重 → Rerank 精排 → 双闸门兜底(0.6/0.35)
→ 截取 top_k → 补 title 溯源 → 落库 kb_search_logs → 写缓存 → 返回
```

- ▶ **与 step4/02 §四.2 差异**：规范要求"同 doc_id 多分块只保留最高分"；V1 实现仅按 chunk_id 去重。V2 对齐（见 §11.3 V2-T10）

### 6.4 降级矩阵（5 重 + 扩展，对齐 step6/01、step3/03）

| 故障场景                 | 降级路径             | 响应/错误码                                                  | 告警       |
| :----------------------- | :------------------- | :----------------------------------------------------------- | :--------- |
| Milvus 宕机              | M1：中止检索。ES-only 为 **非一期强制** 预案 | M1：A3005。二期开 ES 开关后可 degraded=true                  | Warning    |
| ES 宕机                  | Milvus-only 检索（一期默认即此路径） | degraded 可不置（一期无 ES）                                 | Warning    |
| 双侧宕机                 | 不检索               | A3005                                                        | Critical   |
| Rerank 宕机              | RRF score 排序（**非一期强制**；M1 不调用 rerank） | degraded=true                                                | Warning    |
| Embedding 宕机（入库侧） | 中止入库             | A3003, status=failed（禁止跳过向量化仍 active/可检索）       | Critical   |
| Embedding 宕机（检索侧） | 中止检索             | A3003                                                        | Critical   |
| Redis 故障（缓存/限流）  | 静默绕过             | 正常返回                                                     | Warning    |
| 检索融合分 < 0.6         | 判定无匹配（闸门 1） | degraded 保持 false（业务判定非基础设施降级，V1-T17 已接入） | 命中率指标 |
| rerank_score < 0.35      | 兜底文案（闸门 2）   | code=0, results=[]                                           | —          |

降级总原则（对齐 step3/03）：检索为空/失败**禁止抛异常给终端**，调用方（call-service）走 FAQ 兜底。

### 6.5 在线大模型回答（V5，开关门控）

```
POST /search (answer=true) → 检索（§6.3）→ 若 KB_LLM_ANSWER_ENABLED=true 且未触发兜底
→ 组装上下文（top_k chunk 文本 + 溯源）→ LLM Provider.generate()
→ 返回 answer（含引用 doc_id）→ 落 kb_search_logs（llm 调用标记）
→ LLM 超时/熔断/不可用 → answer=null + degraded=true，仅返回检索结果
```

- 开关关闭或请求未开启 answer → 行为与 V4 完全一致，不产生任何 LLM 调用（零成本回退）
- LLM 失败（超时/熔断/Provider 不可用）→ 降级为纯检索结果，禁止抛错给前端（对齐降级总原则）
- §1.4 边界：仅当 `KB_LLM_ANSWER_ENABLED=true` 时 rag-service 才向外调用 LLM，属显式放开的受控例外；LLM Provider 抽象（在线/本地）为后续本地部署预留

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

- ▶ A3004（相关性不足）与 A3006（rerank 失败）**仅内部/日志码，禁止作为 HTTP `code` 返回**：对外仍 `code="0"` + message / `degraded=true`（**与 step6/01 一致，不是漏实现**）
- ▶ V5：LLM 回答失败**不引入新错误码**——超时/熔断/不可用一律降级为纯检索结果（answer=null + degraded=true），与 V4 行为一致（对齐"业务错误统一 200 封装"）
- ▶ 系统异常（B0007/B0008/B0009）由全局 handler 统一处理，本服务不自行返回

---

## 8. 可配置项（对齐 step6/01 重试超时、step5/01 环境）

| 键                             | 默认值     | 说明                                                                                          |
| :----------------------------- | :--------- | :-------------------------------------------------------------------------------------------- |
| KB_CHUNK_SIZE                  | 500        | 分块大小                                                                                      |
| KB_CHUNK_OVERLAP               | 50         | 分块重叠                                                                                      |
| KB_CHUNK_MIN_LENGTH            | 10         | 最短分块长度                                                                                  |
| KB_RETRIEVAL_TOP_N             | 20         | 每路召回条数                                                                                  |
| KB_RRF_K                       | 60         | RRF 融合参数                                                                                  |
| KB_RERANK_THRESHOLD            | 0.35       | 兜底闸门 2 阈值（rerank 分）                                                                  |
| RAG_SCORE_THRESHOLD            | 0.6        | **全局检索阈值**（闸门 1，对齐 step1/01，V1 待接入流程）                                      |
| KB_CACHE_TTL                   | 3600       | 查询缓存 TTL（秒）                                                                            |
| KB_CACHE_ENABLED               | true       | 查询缓存开关                                                                                  |
| KB_MAX_FILE_SIZE               | 20MB       | 上传大小上限                                                                                  |
| KB_PARSE_TIMEOUT               | 30s        | 解析超时                                                                                      |
| KB_HEALTH_DEGRADED_MODE        | false      | /health 降级模式：true 时依赖探活失败只告警，status=degraded 不判 unhealthy（不触发容器重启） |
| KB_EMBED_TIMEOUT/RETRIES/BREAK | 5s / 3 / 3 | 对齐 step6/01 通用外部调用参数                                                                |
| RAG_P95_TARGET                 | 3s         | 检索 P95 时延目标（对齐 step6/02 全局 3s）                                                    |
| KB_LLM_ANSWER_ENABLED          | false      | V5：检索接入在线大模型回答开关（关闭=V4 行为，零 LLM 调用）                                   |
| LLM_PROVIDER                   | dashscope  | V5：LLM Provider（dashscope 在线 / local 本地 OpenAI 兼容）                                   |
| LLM_TIMEOUT                    | 5s         | V5：LLM 回答超时（超时→降级纯检索，不抛错）                                                   |
| KB_IMAGE_EXTRACT_ENABLED       | false      | V5：PDF/DOCX/PPTX 内嵌图片抽取开关（关闭=仅文本，V4 行为）                                    |
| KB_TABLE_EXTRACT_ENABLED       | false      | V5：内嵌表格结构化解析开关（关闭=表格文本化，V4 行为）                                        |
| KB_EXCEL_SHEET_ALL             | true       | V5：Excel 全部 sheet 入库（false=仅首个 sheet）                                               |
| KB_EXCEL_HEADER_ROW            | 1          | V5：Excel 表头行号（约定首行为表头，行切块列名前缀）                                          |

---

## 9. 独立测试方案（已落地，240 passed / 1 skipped）

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
| 检索（M1=Milvus） | M1 验收稠密检索、溯源、分类过滤；双路召回/RRF 为非一期强制 | test_search.py                              |
| 双闸门兜底        | 闸门 1：稠密相似度<0.6 无匹配；闸门 2：rerank<0.35 兜底   | test_gate_threshold.py / test_search.py     |
| 分页              | 仅 page + page_size（默认 20、最大 100）；禁止 size 别名 | test_pagination.py                          |
| 缓存兜底隔离      | 兜底/降级响应不写缓存                                     | test_query_cache.py                         |
| 缓存命中落日志    | 命中写 kb_search_logs（cache_hit='1'）                    | test_query_cache.py                         |
| 兜底              | rerank<0.35 → results=[] + 文案                           | test_search.py                              |
| 降级              | M1：Milvus 宕机→A3005。ES-only / Rerank→RRF 为 **非一期强制** 预案 | test_search_degradation.py                  |
| 缓存              | 写入/命中/TTL/失效/容错                                   | test_query_cache.py                         |
| 限流              | 放行/429+B0006/用户维度/故障降级                          | test_rate_limit.py                          |
| 权限              | B0002 / B0003 / ADMIN+SUPERVISOR 放行                     | test_require_role.py                        |
| 敏感词            | 存在→text/plain 原文 / 缺失→404                           | test_sensitive_words_api.py                 |

### 9.3 冒烟脚本

`scripts/smoke/smoke_rag.sh`：health → 上传 TXT（带 ADMIN token）→ 列表 → 检索 → 删除，5 步全 PASS。

---

## 10. 版本演进路线（V1 → V5）

### 10.1 版本能力边界总览

```
V1（已实现基础）             V2（多模态+智能检索）       V3（多模态深化）         V4（摘要+收口）          V5（本期规划）
┌───────────────────┐    ┌────────────────────────┐   ┌──────────────────┐   ┌──────────────┐   ┌────────────────────┐
│ 4格式解析入库       │    │ 图片OCR / 音频ASR        │   │ VAD静音裁剪       │   │ 文档级摘要      │   │ Excel/PPT 新格式    │
│ Milvus稠密检索（M1） │──▶│ Query改写 / 动态路由     │──▶│ 开场白规则裁剪     │──▶│ ES摘要加权检索   │──▶│ 内嵌图片/表格抽取   │
│ 混合检索+RRF+Rerank  │    │ 业务加权 / 行级权限      │   │ 解析失败降级       │   │ 遗留修复(D7/D8) │   │ AMR/WMA 音频扩展    │
│ （非一期强制/二期）  │    │ 多轮对话               │   │ Embed/ES降级      │   │               │   │ Gradio 前端入口     │
│ 兜底/溯源/日志      │    │ 敏感词DB表+热刷新        │   │ 摘要元数据        │   │               │   │ 在线大模型(开关)    │
│ 缓存/限流/权限      │    │ 双闸门0.6接入/同doc去重  │   │                  │   │               │   │                    │
│ 降级矩阵（5重）     │    │                          │   │                  │   │               │   │                    │
│ 敏感词只读(文件)    │    │                          │   │                  │   │               │   │                    │
└───────────────────┘    └────────────────────────┘   └──────────────────┘   └──────────────┘   └────────────────────┘
```

### 10.2 迭代明细

#### V1（本期）— 最小闭环

- **核心目标**：RAG 检索链路可落地、可验收。**M1 验收只要求 Milvus 稠密检索**（对齐 step1/04）。
- **能力**：见 §1.2（13 项）；**必做（M1 部署）**：文档入库/删除/reindex（Milvus）、稠密检索、兜底、溯源、日志、分类隔离、缓存、限流、权限、敏感词只读、Embedding 失败置 failed+A3003。**ES BM25 / RRF / Rerank 非一期强制**（代码可含开关，覆盖矩阵一期默认关；M1 不强制部署 ES/rerank-service）。**不做**：见 §1.3
- **技术取舍**：一期仅 Milvus；固定 RRF / ES BM25 / rerank 0.35 闸门为二期预案；单轮无会话；PG 日志表替代 LangSmith；敏感词文件化
- **风险与已控**：Milvus gRPC 代理坑（docker-compose 清代理变量）；langchain 依赖冲突（延后）；Embedding 依赖第三方（超时+重试+熔断；失败 failed+A3003）

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
  8. **敏感词整改**：文件 → sensitive_words 表 + `rag:sensitive_words:version` + `kb.sensitive_updated` Pub-Sub（qc 60s 热刷新；stale 时钟见 step6/02）
  9. **检索整改**：双闸门 0.6 接入检索流程；同 doc_id 只保留最高分去重
  10. **存储整改**：Milvus 增 version/source 字段（一次性重建索引）；ES 索引名统一；Redis key 冒号化 + 收录 step4/03。kb_search_logs 类型已收口 step4/01 CHAR(1)，不再 BOOLEAN→VARCHAR
- **技术选型取舍**：OCR/ASR 独立 sidecar 服务（依赖重，不污染主镜像）；Query 改写复用 DashScope（不新增供应商）；意图分类先规则后模型
- **风险点**：PaddleOCR 体积大/CPU 慢 → 独立服务 + 异步入库 + 队列削峰；LLM 改写不确定性 → 置信度降级；索引重建窗口 → 发布窗口内全量 reindex（接受短时不可用）；多轮无状态 → Redis session + 超时清理
- **不做**（推 V3）：LangSmith、评估模块、版本灰度

#### V3 — 多模态深化（已实现）

- **核心目标**：强化多模态解析链路的质量与容错
- **新增能力**：VAD 静音裁剪（RMS 阈值）+ ≤60s 分片逐片转写拼接；通话开场白规则裁剪（`KB_ASR_OPENING_TRIM_ENABLED`，纯规则零 LLM）；OCR/ASR 解析失败降级（`KB_OCR_ASR_DEGRADED_KEEP_TEXT`，占位入库 degraded=true）；ASR Provider 切换（DashScope/Whisper）
- **技术取舍**：开场白裁剪用纯规则（不引入 LLM，保证确定性）；分片转写拼接复用既有 ASR 熔断（`rag:asr:degraded` TTL 60s）
- **风险点**：规则裁剪误伤正文 → 开关门控 + 关闭保留原样

#### V4 — 摘要元数据 + 遗留收口（已实现）

- **核心目标**：提升多模态检索命中率 + 关闭历史债务
- **新增能力**：文档级摘要元数据（上传携带/PUT 补全；PG 权威 + ES `doc_summary` 检索副本 + `KB_SUMMARY_ENABLED` 加权检索；响应 `summary` 字段契约）；ASR 开场白规则版落地（D8）；main.py 敏感词 seed 重构（D7）；历史文档漂移修正（W4/W5/W6）
- **技术取舍**：摘要写入顺序 ES→PG 防脏状态；摘要仅作检索加权，不替换 chunk 内容
- **风险点**：摘要来源分散 → 以 PG 为单一权威 + ES 副本可重建

#### V5 — 多模态混合检索增强 + 浏览器前端 + 在线大模型（本期规划）

- **核心目标**：补齐真实数据源缺口（Excel/PPT/AMR/WMA）、提升多模态检索质量（内嵌图片文搜图、内嵌表格结构化）、提供 Gradio 浏览器前端入口、检索经开关接入在线大模型回答
- **新增能力与验收标准**：

1. **Gradio 浏览器前端入口**
   - 页面：上传页（全部支持格式）+ 问答页（问题 → 召回 chunk → LLM 回答全链路展示）
   - 召回片段含图片/表格预览与溯源元数据（doc_id/来源/页码）
   - 异常友好提示（后端宕机/LLM 熔断不白屏、不 500）
   - 【验收：浏览器打开无报错；上传成功返回 doc_id、失败页面提示；问答完整展示全链路结果】
2. **文档格式新增 Excel、PPT，兼容 PDF/Word**
   - .xlsx：全部 sheet 入库、首行=表头、行切块（每行序列化"列名1:值1, 列名2:值2,..."）、sheet 名作前缀冗余进每行
   - .pptx：每页抽取 标题+正文+演讲者备注（备注并入页文本），过滤页码/版式占位文字（防误删正文）
   - 老格式解析与 V4 完全一致，无回归
   - 【验收：Excel/PPT 正常入库可检索；存量格式全量回归通过】
3. **PDF/PPT/Word 内嵌图片与表格解析**
   - 内嵌 PNG/JPEG、位图、矢量图（渲染为位图）抽取 → OCR 文本索引入库 → 支持文搜图；记录文档、页码元数据
   - 内嵌表格完整解析，保留行列结构（不打散表格）→ 问答可命中任意单元格
   - 表格/图片在检索结果中可预览（对应 §1.3 Gradio 展示）
   - 【验收：抽取图片可被文本查询命中并返回；问答命中表格任意单元格；结果含文档/页码元数据与预览】
4. **音频格式扩展：AMR、WMA（兼容 MP3/WAV/M4A）**
   - AMR/WMA 电话录音正常转写、分块、入库、检索（VAD/分片策略对齐既有 ASR 抽象）
   - 损坏音频返回业务错误（A3001 或降级占位），不卡死 worker
   - 【验收：AMR/MP3/WMA/WAV 四种格式可转写入库检索；损坏音频业务错误返回且 worker 不卡死】
5. **RAG 检索接入在线大模型（开关门控）**
   - `KB_LLM_ANSWER_ENABLED=false` 默认关闭；开启时检索后生成带溯源的 LLM 回答，关闭时行为与 V4 完全一致
   - LLM Provider 抽象（在线 DashScope / 本地 OpenAI 兼容），为后续本地部署预留
   - LLM 超时/熔断/不可用 → 降级为纯检索结果（degraded=true），不抛错
   - 【验收：开关开启返回 answer+召回片段；关闭与 V4 行为一致；停 LLM 服务检索仍正常】

- **技术取舍**：图片入库先走「OCR 文本索引 + 图片文件存储 + kb_doc_assets 元数据」实现文搜图（图片向量/CLIP 推后）；Gradio 独立进程，复用现有 /kb/\* API；AMR/WMA 解码走 ffmpeg（服务端依赖），解码失败按既有解析失败路径处理
- **风险点**：内嵌图片/表格抽取的格式多样性（矢量图渲染、合并单元格）→ 开关门控 + 解析失败按既有 A3001 路径；LLM 回答不确定性 → 开关默认关 + 超时/熔断降级；Gradio 仅内部使用不接公网
- **不做**（推后续）：图片向量检索（CLIP）、扫描件 PDF 整本 OCR、知识图谱、治理能力（LangSmith/评估/版本灰度/敏感词 CRUD）

### 10.3 版本迁移兼容策略

| 维度            | 策略                                                                                                                                                                                                                                                                          |
| :-------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **数据库**      | 只加列/加表，不改既有列语义；status 扩展走 Alembic 增量迁移；kb_search_logs 布尔列已是 CHAR(1) `'0'|'1'`（step4/01）；kb_document_versions 新增表                                                                                                                               |
| **接口**        | 对外契约（路径/响应包/错误码）向后兼容；新能力新增端点或可选字段（缺省=V1 行为）；分页**仅** `page` + `page_size`，**禁止 `size` 别名**（立即废弃，无兼容期）；top_k 上界定稿为 20（不随版本放宽）；category 四值边界永不收紧                                                                               |
| **索引**        | Milvus/ES 只增字段不删字段；version/source 增列在发布窗口一次性重建，且校验 kb_documents.status 与索引一致                                                                                                                                                                    |
| **缓存**        | 缓存 key 引入版本段（`rag:cache:query:v2:{md5}`），旧 key 自然过期不迁移                                                                                                                                                                                                      |
| **检索行为**    | Query 改写/动态路由/业务加权/双闸门 0.6 均以开关引入（KB_QUERY_REWRITE_ENABLED 等），关闭时行为与 V1 完全一致，灰度期可一键回退                                                                                                                                               |
| **数据**        | 业务加权/行级权限元数据由 data-platform 推送增量回填，不要求存量一次补齐                                                                                                                                                                                                      |
| **敏感词**      | V2 切换 DB 表数据源时，以 seed 迁移同步 .dict 文件内容 → sensitive_words 表，保持 50 词基线不漂移                                                                                                                                                                             |
| **V5 新增能力** | Excel/PPT 解析、内嵌图片/表格抽取、AMR/WMA 音频、LLM 回答全部以开关引入（KB_LLM_ANSWER_ENABLED 等），关闭时行为与 V4 完全一致；图片存储 + kb_doc_assets 为新增表/目录，不影响存量；检索响应新增 answer/assets 可选字段，缺省=V4 行为；Gradio 复用既有 /kb/\* API 不动后端契约 |

---

## 11. 开发任务拆分

### 11.1 任务卡说明

优先级：P0=链路阻断/验收必过；P1=生产必需非阻断；P2=增强项。依赖：前置任务。交付物：独立可验收产出。版本归属：V1 必做（已全部完成 ✅）/ V2 / V3 / V4 / V5。

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
| V1-T18 | 整改：分页仅 page + page_size，禁止 size 别名（对齐 step5/02；立即废弃兼容回退） | P2     | T7       | models/document.py 去掉 size 别名 + 测试                  | ✅   |
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
| V2-T11 | 存储整改：Milvus 增 version/source（重建索引）、ES 索引名统一、Redis key 冒号化（kb_search_logs 类型已收口 step4/01 CHAR(1)） | P1     | V2-T9/T10 | 迁移 0004 + 重建脚本          |

**V2 验收标准**：OCR 图片可检索命中；Query 改写开关关闭时行为与 V1 一致（可回退）；敏感词热刷新 60s 内 qc 侧生效；整改项全量回归 65+ 用例通过。

### 11.4 V3 任务清单（规划，不排期）

| #     | 任务                                             | 优先级 | 依赖  |
| :---- | :----------------------------------------------- | :----- | :---- |
| V3-T1 | LangSmith 追踪接入（与 PG 日志并存）             | P1     | —     |
| V3-T2 | 评估模块（离线评测集 + 召回率/幻觉率）           | P0     | V3-T1 |
| V3-T3 | 文档版本管理 + 灰度（kb_document_versions 新表） | P1     | V3-T2 |
| V3-T4 | Prompt 模板动态配置                              | P2     | —     |
| V3-T5 | 敏感词 CRUD（只读升级全量管理）                  | P2     | V2-T9 |

### 11.5 V5 任务清单（迭代规划）

| #      | 任务                                                                                                                              | 优先级 | 依赖     | 交付物                                                |
| :----- | :-------------------------------------------------------------------------------------------------------------------------------- | :----- | :------- | :---------------------------------------------------- |
| V5-T1  | 依赖引入：openpyxl / python-pptx / PyMuPDF(fitz) / ffmpeg+pydub / gradio + 格式白名单扩展（.xlsx/.pptx/.amr/.wma）                | P0     | —        | requirements + parsers 白名单（SUPPORTED_EXTENSIONS） |
| V5-T2  | Excel 解析：全部 sheet、首行=表头、行切块（列名:值 序列化）、sheet 名前缀、空行/全空值跳过                                        | P0     | V5-T1    | xlsx_parser.py + 测试                                 |
| V5-T3  | PPT 解析：标题+正文+演讲者备注（备注并入页文本）、页码/版式占位过滤                                                               | P0     | V5-T1    | pptx_parser.py + 测试                                 |
| V5-T4  | 内嵌图片抽取：PDF/DOCX/PPTX（PNG/JPEG/位图/矢量图渲染）+ OCR 文本 chunk 入库（source=asset_image）+ kb_doc_assets 元数据 + 文搜图 | P0     | V5-T2/T3 | asset_extractor.py + 迁移 + 测试                      |
| V5-T5  | 内嵌表格结构化解析：保留行列结构不打散，问答命中任意单元格，检索结果可预览                                                        | P1     | V5-T2/T3 | 表格解析器 + 测试                                     |
| V5-T6  | 音频扩展：AMR/WMA 解码（ffmpeg）+ ASR 接入（VAD/分片策略对齐），损坏音频业务错误不卡 worker                                       | P1     | V5-T1    | 音频解码 + 测试                                       |
| V5-T7  | Gradio 前端：上传页 + 问答页（问题/召回 chunk/图片表格预览/LLM 回答全链路）+ 异常友好提示                                         | P0     | V5-T2~T6 | gradio/app.py + 冒烟                                  |
| V5-T8  | LLM Provider 抽象（在线 DashScope / 本地 OpenAI 兼容）+ KB_LLM_ANSWER_ENABLED 门控 + 超时/熔断降级                                | P1     | —        | llm_service.py + 测试                                 |
| V5-T9  | 检索响应扩展：answer（带溯源）+ results[].assets 可选字段，缺省=V4 行为                                                           | P1     | V5-T8    | api/search.py + models/search.py + 测试               |
| V5-T10 | 全量回归（存量不回退）+ 冒烟脚本扩展 + V5 验收                                                                                    | P0     | V5-T1~T9 | smoke_rag_v5.sh + 验收                                |

**V5 验收标准**：Excel/PPT 正常入库可检索且存量格式零回归；内嵌图片文搜图命中、内嵌表格命中任意单元格；AMR/MP3/WMA/WAV 四种音频可转写入库、损坏音频业务错误不卡 worker；Gradio 页面全链路展示 + 异常友好提示；`KB_LLM_ANSWER_ENABLED` 开关开启返回 answer、关闭与 V4 行为一致、停 LLM 服务检索仍正常；全量测试 + 冒烟 PASS。

---

## 12. 落地现状对照（2026-08-21）

- rag-service-v1 变更：47/47 任务完成，openspec validate 通过
- 全量测试：**77 passed**（含 V1-T17~T20 新增 12 用例）；冒烟：smoke_rag.sh 全链路 PASS
- 实测：限流 61 次→429+B0006；权限 401/403/放行三态；缓存 2.06s→0.003s；删除后缓存失效正确；**双闸门 0.6 已接入（闸门 1 稠密相似度<0.6 判定无匹配）**；**分页仅 page_size（禁止 size 别名）**；**缓存命中落日志实测 cache_hit='1'（1ms）vs 真实检索 cache_hit='0'（2817ms）**；**兜底/降级响应不写缓存**
- **V1 整改待办已清零**；剩余规范对齐项均为 V2（敏感词 DB 表化、Milvus/ES schema、Redis key 收录）。kb_search_logs 类型与 kb_documents.status **已同步 step4/01**
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
| A7  | 表类型规范                     | 枚举/布尔一律 VARCHAR+CHECK，禁止 BOOLEAN（step4/01 §1.2）                                                | kb_search_logs.degraded/is_fallback/cache_hit 为 CHAR(1) `'0'|'1'`（step4/01 已收录）                         | **已同步 step4/01**                                          |
| A12 | kb_documents.status            | CHECK ('processing','active','archived','failed')（step4/01）                                          | 四值状态机                                                                                                       | **已同步 step4/01**                                          |
| A8  | 分页字段                       | 仅 page/page_size（默认 20 最大 100）；禁止 size 别名（step5/02）                                          | 列表接口曾用 size                                                                                                | **审阅 36**：立即废弃 size，仅 page_size 为现行契约 |
| A9  | Redis key 收录                 | step4/03 key 清单仅 3 项                                                                                  | V1 新增 `rag:cache:query:*`/`rag:cache:keys:*`/`rag:ratelimit:*`/`rag:embed:degraded`/`rag:rerank:degraded` 5 类 | 待 step4/03 收录                                             |
| A10 | Redis key 命名（规范自身冲突） | step4/03 要求冒号分隔 `<prefix>:<entity>:<id>`                                                            | 熔断标识用下划线（同 step6/01 `{service}:llm_degraded` 风格）                                                    | 规范层统一，V2 随 T11 调整                                   |
| A11 | Embedding 失败处理             | 失败置 `kb_documents.status=failed` + A3003；检索不得命中无向量文档（step6/01 §6.1，已与本文对齐） | 失败置 status=failed + A3003（防无向量脏数据）                                                                   | **已与 step6 对齐**；禁止「跳过向量化仍入库/仍可检索」 |
| A13 | 数据中台对接                   | 检索日志入 dw（etl_task_logs/llm_call_logs）（step4/02 §四.4）                                            | 仅本地 kb_search_logs 落库，未接订阅/ETL 管道                                                                    | V2 数据管道接通（被动订阅）                                  |
| A14 | Redis 规范自身 DB 编号         | step4/03 §3.1/3.2 小标题 DB 编号差一位（DB1/DB2 互换）                                                    | —（不影响实现，DB2 已按正文生效）                                                                                | 规范层修正，与本服务无代码改动                               |

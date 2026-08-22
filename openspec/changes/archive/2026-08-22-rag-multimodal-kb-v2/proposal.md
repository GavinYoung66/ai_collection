## Why

V1 RAG（rag-service）已完成 PDF/DOCX/MD/TXT 4 格式文本文档的混合检索闭环，但催收业务场景中大量知识以制度截图、表格、通话录音等**非文本载体**存在，坐席使用 "逾期协商还款" 等模糊自然语言查询时召回精准度不足。同时 §10 演进路线明确要求 V2 完成 **V1 遗留的 4 项规范对齐整改**（敏感词 DB 表化、Milvus/ES schema 对齐、kb_search_logs BOOLEAN→VARCHAR、Redis key 冒号化收录 step4/03）。基于 §10.3 兼容策略（只加列/开关降级/缓存 key 版本化）在**不破坏 V1 既有能力**的前提下，扩展 rag-service 为多模态知识库。

## What Changes

- **新增**：图片解析能力（.png/.jpg→PaddleOCR 独立服务→识别文本→分块→双索引），支持制度截图、表格识别，独立容器化部署不污染主镜像
- **新增**：音频解析能力（.mp3/.wav/.m4a→ASR Provider 抽象→转写文本→分块→双索引），通话录音按质检规则过滤空段和静音
- **新增**：智能检索管道：Query 改写（LLM 纠错/术语归一/扩展，置信度<0.6 降级原 query）+ 动态路由（意图规则分类→权重调整），开关关闭时行为与 V1 完全一致
- **新增**：检索增强：业务加权（kb_documents 增 customer_id/agent_id/call_date 元数据，新通话 ×1.5、高危 ×1.3）+ 行级权限（角色/部门过滤 KB 分类，tenant_id 预留）+ 同 doc_id 只保留最高分去重（对齐 step4/02 §四.2）
- **新增**：多轮对话：Redis session（v2 前缀） 保存上下文，追问澄清；会话超时 30min 自动清理
- **新增**：敏感词整改：数据源从 `.dict` 文件切换到 `sensitive_words` 表（category/severity/status 完整字段），引入 `rag:sensitive_words:version` 版本号，更新后 PUBLISH `kb.sensitive_updated`，qc-service 60s 内热刷新
- **修改**：文档入库（document-ingest）：扩展格式白名单（png/jpg/mp3/wav/m4a）、新增外部解析器异步分发、支持 Embedding 失败降级入库（仅 ES 文本索引，开关 KB_EMBED_DEGRADED_KEEP_TEXT，默认关闭=V1 行为失败直接 A3003）
- **修改**：混合检索（hybrid-search）：增加 4 个开关接入点（`KB_QUERY_REWRITE_ENABLED` / `KB_ROUTING_ENABLED` / `KB_WEIGHTING_ENABLED` / `KB_ROW_LEVEL_ENABLED`，默认全 false=V1 行为）；缓存 key 增加 v2 段 `rag:cache:query:v2:{md5}`；溯源字段新增 `version` / `source`
- **修改**：检索日志（search-logging）：新增 `cache_hit`（V1 已实现待 spec 同步）、`query_original` / `query_rewritten`、`intent_tag` 字段；`degraded` / `is_fallback` BOOLEAN → VARCHAR('0','1') + CHECK，外部语义不变（V2-T11 存储整改）
- **存储整改（实现级，非 spec 能力）**：Milvus collection `kb_vectors` 增 `version`(INT64) / `source`(VARCHAR255) 字段，发布窗口一次性重建索引；ES index `kb_bm25` 改名为规范名 `kb_chunks_bm25` 并双写过渡期；**Redis 熔断标识 `rag:embed:degraded` / `rag:rerank:degraded` 沿用 V1 命名（与 step6/01 `{service}:llm_degraded` 熔断风格一致）**，仅在 step4/03 规范待办清单中补充收录 rag-service 新增 V2 key（rag:session:v2:*、rag:ocr:degraded、rag:asr:degraded、rag:rewrite:degraded 等），**不做代码级改 key**（V1 已落地，风险大于收益，且无调用方依赖）。
- **向后兼容保证**：所有新能力默认开关关闭=V1 行为；旧缓存 key 自然过期不迁移；数据库只加列/加表不改既有列语义；接口路径/响应包/错误码完全兼容

## Capabilities

### New Capabilities

- `rag-service/multimodal-ingest`: 多模态解析入库——图片 OCR（PaddleOCR 独立服务 `/ocr`，png/jpg，30s 超时+重试+熔断）与音频 ASR（Provider 抽象，DashScope/Whisper 可切换，mp3/wav/m4a，静音裁剪与说话人分离），统一走现有 IngestService 的 parser 分发，分块/向量/双索引复用 V1 链路。
- `rag-service/smart-retrieval-pipeline`: 智能检索管道——Query 改写链（DashScope LLM：纠错/术语归一/扩展，返回 `rewritten_query + confidence`，confidence<0.6 降级原 query 并标 degraded）+ 动态意图路由（规则版关键词匹配→命中则按意图加权调整召回类别权重，意图规则文件可配置）。全链路开关控制，关闭=V1 行为。
- `rag-service/search-enhancements`: 检索增强三项——业务加权（scoring 融合阶段读取 kb_documents 元数据，新业务 ×1.5、高风险 ×1.3，开关）+ 行级权限过滤（从 JWT 取 role/dept 映射到 category 白名单，无匹配直接过滤，开关）+ 同 doc_id 去重（对齐 step4/02，融合后按 doc_id 只保留最高分 chunk，无开关，纯规范对齐不影响 V1 已有结果语义）。
- `rag-service/conversational-search`: 多轮对话检索——Redis `rag:session:v2:{session_id}` 保存最近 10 轮上下文，`session_id` 来自请求头 `X-Session-Id`（ULID，缺失自动生成），TTL=1800s；追问时将历史上下文拼接作为重写附加输入；超时/空 session 走单轮 V1 流程。
- `rag-service/sensitive-words-sync`: 敏感词热刷新机制——读取 PG `sensitive_words` 表（status=active）替代内置 `data/sensitive_words.dict`；版本号 `rag:sensitive_words:version` Redis INCR；更新后 Redis PUBLISH `kb.sensitive_updated`（payload=version）；`/sensitive-words/current.dict` 继续 text/plain 输出，但内容来自 DB；qc-service 60s 内拉取重建自动机。seed 数据迁移后 50 词基线保持不变。

### Modified Capabilities

- `rag-service/document-ingest`: 扩展格式白名单（新增 .png/.jpg/.mp3/.wav/.m4a，解析失败仍 A3001，不破坏旧格式语义）；ParserFactory 增加 OCR/ASR 分支（外部 HTTP 调用）；新增可选降级模式 KB_EMBED_DEGRADED_KEEP_TEXT（默认关闭=V1 行为，Embedding 失败直接 failed+A3003；开启则仅写 ES 文本索引，无向量，标 degraded 入日志，对齐 step6/01 §6.1 降级入库口径）。
- `rag-service/hybrid-search`: 在现有检索流程中引入 4 个开关接入点（QueryRewrite / Routing / Weighting / RowLevelAccess，默认全 false=V1 行为）；融合后同 doc_id 最高分去重；缓存 key 段升级 `rag:cache:query:v2:{md5}`，旧 V1 key 不迁移；溯源字段新增 `version`（INT）/`source`（str）（Milvus schema 新增字段冗余回填，缺省为 null）；对外响应包 code/message/data/trace_id 保持不变。
- `rag-service/search-logging`: 在现有 15 字段基础上新增 `cache_hit`（VARCHAR，'0'/'1'）、`query_original`、`query_rewritten`、`intent_tag`、`session_id` 共 5 字段；`degraded` / `is_fallback` 从 BOOLEAN 迁移为 VARCHAR CHECK('0','1')，默认值='0'，应用层读写语义不变；写入失败仍然不阻断主流程。

## Impact

- **代码目录**：`services/rag-service/app/` 六层架构全部受影响；新增 `app/parsers/image_parser.py` / `audio_parser.py`（Provider 抽象）；新增 `app/services/ocr_client.py` / `asr_client.py` / `query_rewrite_service.py` / `routing_service.py` / `weighting_service.py` / `row_access_service.py` / `session_service.py` / `sensitive_words_sync.py`；`app/models/` 新增 pydantic 字段；`app/api/documents.py` / `search.py` 扩展入口参数与开关接入；`main.py` 注册新路由 + startup 建 Milvus 新字段 schema + 订阅 Pub-Sub；`app/vectorstore/milvus_client.py` / `es_client.py` 升级 schema
- **数据库迁移**：新增 0003（kb_documents 增 customer_id/agent_id/call_date/risk_tag 元数据列 + sensitive_words 表按 step4/01 DDL 建立并 seed 50 词基线）、0004（kb_search_logs 增 cache_hit/query_original/query_rewritten/intent_tag/session_id 共 5 字段 + BOOLEAN→VARCHAR 双写过渡 + 旧 query 列内容回填到 query_original）；多轮会话纯 Redis 存储，不新增 KB 关系表。存量 V1 文档缺省元数据值允许 null，不由 rag-service 回填（data-platform 推送增量填）
- **外部依赖**：新增 `deploy/ocr/` PaddleOCR 独立容器（端口 8200，HTTP /ocr）；`deploy/asr/` ASR Provider 抽象优先复用 DashScope（不新增供应商，与 call-service LLM 同账号）；LLM Query 改写走 DashScope `/api/v1/services/aigc/text-generation/generation`；Redis Pub-Sub 新增 channel `kb.sensitive_updated`
- **接口契约**：`POST /kb/documents` 格式白名单扩展（不新增路径，不修改请求包，不新增字段）；`POST /kb/search` 请求体新增可选 `session_id`（str）、`rewrite`（bool，默认 false=V1 行为）；响应包结构不变；`GET /sensitive-words/current.dict` 行为不变（数据源迁移不可见）
- **测试影响**：V1 77 用例必须**全量回归通过**（开关全 false 时行为一致）；新增 OCR/ASR/QueryRewrite/Routing/Weighting/RowAccess/Session/SensitiveWordSync 8 个 test_*.py 单元测试 + 冒烟脚本扩展 smoke_rag_v2.sh
- **发布窗口要求**：Milvus `kb_vectors` 增 version/source 需发布窗口内全量 reindex（§10.3 索引兼容策略：接受短时不可用或双 collection 切换）；ES `kb_bm25`→`kb_chunks_bm25` 需双写过渡；敏感词切换需 seed DB 与 `.dict` 同步（50 词基线一致）

## Audit-Aligned Known Issues / CI Gate Requirements（2026-08-22 审查后补充，tasks/design/spec 同步修订内容见 design Decision 4/8/10 + Risks 表格最后两栏）

- **P0-1 / 0005 迁移 DROP BOOLEAN 列的 CI 门禁**：`migrations/versions/0005_rag_service_v2_search_logs_upgrade.py` upgrade **不得包含** `DROP COLUMN degraded/is_fallback BOOLEAN` 或 `RENAME COLUMN degraded_tmp/is_fallback_tmp`。发布前执行：
  ```
  grep -E 'DROP COLUMN.*(degraded|is_fallback)[^_]|RENAME.*(degraded_tmp|is_fallback_tmp)' \
    migrations/versions/0005_*.py → 必须退出码非 0
  ```
  真正的 DROP+RENAME 属于发布 30 天观察期后的 Alembic 0006_post_cleanup（对齐 README Runbook Step 6、tasks §3.12、design Decision 10 第 3/4 条），拆分保障 V1 rollback 到 BOOLEAN 写列不崩溃。
- **P0-2 / SIGHUP 路由热加载的 CI 门禁**：`services/rag-service/app/main.py` startup 必须注册 SIGHUP handler。发布前执行：
  ```
  grep -q 'signal.signal.*SIGHUP' services/rag-service/app/main.py → 必须退出码 0
  ```
  Apply 代码修复对齐 tasks §8.5、design Decision 4 "实现检查点"。
- **P0-3 / startup 敏感词 seed 幂等自检的 CI 门禁**：`services/rag-service/app/main.py` startup `on_event("startup")` 函数尾部必须调用 `SensitiveWordsSyncService(...).ensure_seed_and_version_or_warn()`（或等义词），执行 count=50 校验+INSERT ON CONFLICT+version SET 1+最终<50 WARNING。发布前执行：`grep -q 'ensure_seed\|startup_seed\|startup.*sensitive\|SensitiveWordsSyncService' services/rag-service/app/main.py → 必须退出码 0`。Apply 代码修复对齐 tasks §11.6、sensitive-words-sync spec 新增「启动自检幂等」Requirement（3 Scenario）、design Decision 8 Step B+。
- **P1-1 / 闸门 1 量纲三方一致**：本次对齐采用 **选项 B**（代码→文档：hybrid-search spec 明确闸门 1 = Milvus dense cosine<0.6，ES-only 场景下闸门 1 短路跳过，由闸门 2 rerank<0.35 单独兜底）。如后续 Step 5 灰度时监控到 ES-only 场景 is_fallback='1' 比例 > 5%，则切回选项 A（常量 RAG_FUSED_SCORE_THRESHOLD≈0.005，闸门 1 改取 RRF 融合分并覆盖 ES-only 场景），Apply 端做代码修复。所有文档（hybrid-search spec 闸门 1、design Risks 最后 2 行）对该边界条件已做一致性说明。
- **兼容性声明**：以上 4 项为审查后对 planning 工件做的一致性修订，**不修改需求范围、不新增能力**；修订前后 proposal 目标章节（Why/What Changes/Capabilities/Impact 前 6 点）完全保留，无改动。所有 [ ] 待修复 tasks（3.12 / 8.5 / 11.6）均对应已交付规划内容的"代码层面未达声明完成度"，并非需求新增。


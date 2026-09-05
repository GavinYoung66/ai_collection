# rag-service

RAG 混合检索服务（V4 多模态知识库，摘要元数据能力 + 开场白规则裁剪）。

## §A Redis Key Inventory

### V1 保留（禁止代码级 rename）

| Key                             | 用途                            | TTL   |
| ------------------------------- | ------------------------------- | ----- |
| `rag:embed:degraded`            | Embedding 熔断标识              | 60s   |
| `rag:rerank:degraded`           | Rerank 熔断标识                 | 60s   |
| `rag:cache:query:{md5}`         | V1 查询缓存（自然过期，不读写） | 3600s |
| `rag:cache:keys:{category}`     | 缓存 key 索引 Set               | -     |
| `rag:ratelimit:{bucket}:{user}` | 限流计数                        | -     |

> V1 熔断标识与 step4/03 `<prefix>:<entity>:<id>` 格式存在命名风格冲突（step6/01 强约束熔断 key `{service}:llm_degraded` 兼容），但禁止代码级改 key，避免线上数据丢失。已提交 MR 给全局 step4/03 规范文档补充"rag-service 完整 key 清单"附录。

### V2 新增（严格遵循 step4/03 冒号命名）

| Key                                   | 用途                            | TTL   |
| ------------------------------------- | ------------------------------- | ----- |
| `rag:session:v2:{session_id}`         | 多轮会话上下文（LPUSH + LTRIM） | 1800s |
| `rag:cache:query:v2:{md5_with_flags}` | V2 查询缓存（含开关 hash 隔离） | 3600s |
| `rag:ocr:degraded`                    | OCR 熔断标识                    | 60s   |
| `rag:asr:degraded`                    | ASR 熔断标识                    | 60s   |
| `rag:rewrite:degraded`                | Query 改写 LLM 熔断标识         | 60s   |
| `rag:sensitive_words:version`         | 敏感词版本号（INCR）            | -     |

## §B V2 冒烟测试手动运行指引

V2 冒烟脚本位于 `scripts/smoke/smoke_rag_v2.sh`，覆盖 9 步全链路：

1. `/health` 健康检查
2. 上传 TXT 文档（V1 路径）
3. 上传 JPG 图片（OCR 路径，依赖 ocr-service:8200）
4. 上传 MP3 音频（ASR 路径，依赖 DASHSCOPE_API_KEY）
5. 文档列表（4 种 category 过滤）
6. 检索（开关全关闭=V1 行为，带 X-Session-Id 头）
7. 检索（V2 Query 改写，rewrite=true 入参）
8. 删除文档（双索引清理）
9. 敏感词 refresh + text/plain 50 词一致性

### 运行方式

```bash
# 方式 1：Makefile
make smoke-v2

# 方式 2：直接执行
bash scripts/smoke/smoke_rag_v2.sh
```

### 前置条件

- docker compose up 启动 rag-service + PG + Milvus + ES + Redis
- ocr-service 可用（Step 3 OCR 上传，不可用时 WARN 不阻塞）
- DASHSCOPE_API_KEY 已配置（Step 4 ASR + Step 7 Query 改写，未配置时 WARN 不阻塞）
- SMOKE_ADMIN_TOKEN 环境变量或 conda 环境 `ai_collection` 可用（用于签发测试 JWT）

## §C V2 Migrate Runbook

> 来源：`openspec/changes/rag-multimodal-kb-v2/design.md` Migration Plan。发布日生产 6 步部署 & 回滚计划。

### Step 1: Pre-deploy（前一天夜间）

- 部署 `deploy/ocr/` PaddleOCR 容器（3 副本），健康检查就绪（`GET /health`）。
- 跑 Alembic 0004（kb_documents 元数据列 + sensitive_words 表 seed）、0005（kb_search_logs 新列 + 回填）。检查 row 数：sensitive_words count=50（与 .dict 对齐），kb_search_logs degraded_tmp 全量 NOT NULL。
- ES：创建新 index `kb_chunks_bm25`（带新增字段），开启双写开关 `ES_DUAL_WRITE=true`。跑 `scripts/rag/reindex_es_v2.py` 同步 kb_bm25→kb_chunks_bm25。
- Milvus：创建 collection `kb_vectors_v2`（带 version/source），**暂不切换 collection 名探测目标**。

### Step 2: Deploy V2 app

- 滚动发布 rag-service:v2 容器。7 个功能开关默认全 false（=V1 行为，Dockerfile ENV 已设）。
- 全量跑 V1 77 用例回归 → 必须 100% 通过（开关全关行为一致）。
- 跑 `scripts/smoke/smoke_rag_v2.sh`（含 OCR PNG 上传 + ASR MP3 上传 + QueryRewrite + Routing + Weighting + RowLevel + Session 灰度 5% 流量）。

### Step 3: Milvus 切换（发布窗口 3:00-3:30）

- 跑 `scripts/rag/rebuild_milvus_v2.py`：旧 collection → v2（带 version=1, source="v1_legacy"）。校验 count 对齐（旧 collection.count == new collection.count）。
- 执行 Milvus collection 名探测自动切换（无 alias API 操作）。
- 健康检查：`GET /health` 通过（Embedding+Milvus 探活正常）；跑 20 条检索回归，命中溯源 version=1。
- **回滚**：collection 名探测切回旧 collection。V2 应用能读旧 collection schema（缺省 version/source=null）。

### Step 4: ES 切换（紧随 Milvus 切换后）

- 运维切 `ES_READ_INDEX=kb_chunks_bm25`。
- 验证 Step 1 中双写一致（count 对比）。
- **回滚**：切 `ES_READ_INDEX=kb_bm25`。

### Step 5: 灰度开启新能力（发布后 2 天，监控稳定）

7 开关灰度开启时间表模板：

| 时间     | 开关       | 环境变量                                  | 风险                     | 回滚       |
| -------- | ---------- | ----------------------------------------- | ------------------------ | ---------- |
| Day 2 AM | Query 改写 | `KB_QUERY_REWRITE_ENABLED=true`           | 低（confidence 兜底）    | 设回 false |
| Day 2 PM | 动态路由   | `KB_ROUTING_ENABLED=true` + 传标准 YAML   | 低（无命中=全 1.0）      | 设回 false |
| Day 3 AM | 业务加权   | `KB_WEIGHTING_ENABLED=true`               | 低（缺省 1.0 行为）      | 设回 false |
| Day 3 PM | 行级权限   | `KB_ROW_LEVEL_ENABLED=true` + 传矩阵 YAML | 中（默认兼容全放行）     | 设回 false |
| Day 4 AM | 多轮会话   | `KB_MULTITURN_ENABLED=true`               | 低（仅影响改写 history） | 设回 false |

每一步开关开启后观察 2h：step6/02 监控项（检索命中率<60%告警 / Embedding 队列积压>100），出现问题立刻关开关。

### Step 6: Post-cleanup（发布 30 天后，观察无异常）

- DROP Milvus 旧 collection。
- ES 删除 kb_bm25 旧 index。
- Alembic 0006（可选）：删除 kb_search_logs 旧 BOOLEAN 列，重命名 tmp 列。

### 双读切换变量清单

| 变量                          | 默认值          | 切换效果                                 |
| ----------------------------- | --------------- | ---------------------------------------- |
| `ES_READ_INDEX`               | `kb_bm25`（V1） | 切 `kb_chunks_bm25` 读 V2 索引           |
| `ES_DUAL_WRITE`               | `false`         | `true` 时写入双索引（过渡期）            |
| `KB_QUERY_REWRITE_ENABLED`    | `false`         | `true` 启用 LLM Query 改写               |
| `KB_ROUTING_ENABLED`          | `false`         | `true` 启用意图路由加权                  |
| `KB_WEIGHTING_ENABLED`        | `false`         | `true` 启用业务元数据加权                |
| `KB_ROW_LEVEL_ENABLED`        | `false`         | `true` 启用行级权限过滤                  |
| `KB_MULTITURN_ENABLED`        | `false`         | `true` 启用多轮会话上下文                |
| `KB_EMBED_DEGRADED_KEEP_TEXT` | `false`         | `true` 时 Embedding 失败仍写 ES 降级入库 |

#### V4 新增开关（默认全 false = V3 行为）

| 变量                          | 默认值  | 切换效果                                                                     |
| ----------------------------- | ------- | ---------------------------------------------------------------------------- |
| `KB_SUMMARY_ENABLED`          | `false` | `true` 启用摘要元数据检索增强（ES `doc_summary` 加权 + 响应 `summary` 填充） |
| `KB_SUMMARY_ES_BOOST`         | `1.0`   | 摘要字段 BM25 加权分（1.0 = 与正文同权）                                     |
| `KB_SUMMARY_MAX_LEN`          | `200`   | 摘要长度上限（字符），超限返回 B0001                                         |
| `KB_ASR_OPENING_TRIM_ENABLED` | `false` | `true` 启用 ASR 通话开场白规则裁剪（转写文本开头）                           |

#### V5 新增开关与配置（默认全 false = V4 行为）

> `LLM_MODEL` / `LLM_TIMEOUT` 复用既有配置：`LLM_MODEL=qwen-plus`；`LLM_TIMEOUT` 类默认 10，`.env.example` 登记 5 供本地覆盖；Query 改写/LLM Provider 共用。V5 不重复定义、不改默认值。

| 变量                       | 默认值         | 切换效果                                                                                        |
| -------------------------- | -------------- | ----------------------------------------------------------------------------------------------- |
| `KB_LLM_ANSWER_ENABLED`    | `false`        | `true` 且请求 `answer=true` 时检索接入在线大模型生成带溯源回答（关闭=V4 行为，零 LLM 调用）     |
| `LLM_PROVIDER`             | `dashscope`    | LLM Provider：`dashscope` 在线 / `local` 本地 OpenAI 兼容（切换不影响对外契约）                 |
| `LLM_MAX_CONCURRENCY`      | `4`            | LLM 回答并发上限（信号量，超限排队等待，不降级）                                                |
| `LLM_LOCAL_BASE_URL`       | 空             | local Provider 基址（未配置按不可用降级）                                                       |
| `LLM_LOCAL_API_KEY`        | 空             | local Provider 密钥                                                                             |
| `KB_IMAGE_EXTRACT_ENABLED` | `false`        | `true` 抽取 PDF/DOCX/PPTX 内嵌图片 → OCR 文搜图 + `kb_doc_assets` 元数据（关闭=仅文本 V4 行为） |
| `KB_TABLE_EXTRACT_ENABLED` | `false`        | `true` 内嵌表格结构化解析（保留行列结构、命中任意单元格；关闭=V4 文本化）                       |
| `KB_EXCEL_SHEET_ALL`       | `true`         | Excel 全部 sheet 入库（`false`=仅首个 sheet）                                                   |
| `KB_EXCEL_HEADER_ROW`      | `1`            | Excel 表头行号（首行=表头，行切块列名前缀）                                                     |
| `KB_ASR_AMR_WMA_ENABLED`   | `false`        | `true` AMR/WMA ffmpeg 解码 → 复用 VAD/ASR 转写（关闭=白名单外 A3001，V4 行为）                  |
| `KB_ASSETS_DIR`            | `data/assets/` | 内嵌资产本地存储目录（Docker 挂载 volume，与 Gradio 共享预览源）                                |
| `KB_ASSETS_MAX_SIZE`       | `1024`         | 图片渲染最大边长（px，等比缩放）                                                                |
| `KB_DOC_CHUNK_KEEP`        | `5`            | RRF 后同 doc_id 保留 chunk 数上限；实际保留 N = min(请求 top_k, keep)                           |

### 一键回退方案

发布后 48h 内若出现问题，一键关 7 个开关恢复 V1 行为（无状态副作用，无需 DB rollback）：

```bash
# 关闭所有 V2 开关（k8s ConfigMap 或 docker env）
export KB_QUERY_REWRITE_ENABLED=false
export KB_ROUTING_ENABLED=false
export KB_WEIGHTING_ENABLED=false
export KB_ROW_LEVEL_ENABLED=false
export KB_MULTITURN_ENABLED=false
export KB_EMBED_DEGRADED_KEEP_TEXT=false
export ES_DUAL_WRITE=false
# ES 读索引切回 V1
export ES_READ_INDEX=kb_bm25
# Milvus collection 名探测切回旧 collection（见 Step 3 回滚）
```

## §D V4 摘要元数据能力

> 来源：`openspec/changes/rag-multimodal-kb-v4/design.md`（D3 两端写入契约 / D6 / D7 / D8）与 delta spec `summary-metadata`、`multimodal-ingest`。

### 功能说明

- 文档级摘要由**上游业务中台预生成**（RAG-Service 内部禁止 LLM 调用，对齐 PRD §1.4），随入库携带或事后补全；RAG 仅做长度/类型校验、存储、索引与检索返回，不做语义校验。
- 摘要两端存储：**PG `kb_documents.summary`（权威源）** + **ES `kb_bm25.doc_summary`（检索副本，text/ik_max_word）**；Milvus schema 不变、不向 chunk 向量冗余摘要。
- 写入顺序固定 **ES → PG**（PG 为"一致性收口标记"，杜绝"PG 有摘要、索引无摘要"脏状态）。
- 检索增强开关 `KB_SUMMARY_ENABLED=true` 时：ES 路对 `doc_summary` BM25 加权（`KB_SUMMARY_ES_BOOST`，默认 1.0），响应 `results[].summary` 填充 PG 文档级摘要；开关关闭 = V3 完全行为（`summary` 恒为 null、ES query 不变）。
- ASR 开场白规则裁剪（`KB_ASR_OPENING_TRIM_ENABLED=true`）：转写文本拼接后按 `conf/asr_opening_rules.yaml`（纯正则+关键词表，零 LLM）裁剪开头问候/身份/公司名模式；裁剪后为空则不裁剪（保底）。

### API 变更表（新增/不变）

| 接口                                                 | 变更类型             | 说明                                                                                                                                                                                  |
| ---------------------------------------------------- | -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /kb/documents`                                 | 新增 form 字段       | `summary` 可选，超 `KB_SUMMARY_MAX_LEN`（默认 200）返回 B0001；不携带 = 存量行为完全不变                                                                                              |
| `PUT /kb/documents/{doc_id}/summary`                 | **新增接口**         | ADMIN/SUPERVISOR，限流 10/min；请求体 `{"summary": str}`；doc_id 不存在/archived → B0004；ES 写失败退避重试 3 次（1s/2s/4s）仍失败 → 返回失败（复用 A3003 语义），PG 不写（无半状态） |
| `GET /kb/documents/{doc_id}`                         | 新增响应字段         | 详情响应新增 `summary`（可空，来源 PG 权威源）                                                                                                                                        |
| `POST /kb/search`                                    | 新增响应字段         | `results[].summary` 永远存在（可空）；开关开启填充摘要，关闭恒为 null                                                                                                                 |
| `GET /kb/documents`（列表）                          | **完全保持原有不变** | 不新增 summary 字段，响应负载不变                                                                                                                                                     |
| 其余接口（DELETE/reindex/sensitive-words/health 等） | **完全保持原有不变** | 路径、请求/响应包、错误码零改动                                                                                                                                                       |

### 数据模型变更

| 存储                      | 变更                                                                                             | 说明                                                       |
| ------------------------- | ------------------------------------------------------------------------------------------------ | ---------------------------------------------------------- |
| PostgreSQL `kb_documents` | Alembic `0007_kb_documents_summary.py`：新增列 `summary` TEXT NULL                               | 可空、不强制回填；存量文档为 null                          |
| Elasticsearch `kb_bm25`   | `scripts/rag/es_v4_mapping.json`：新增字段 `doc_summary`（text，ik_analyzer/ik_search_analyzer） | 需跑 `reindex_es_v4.py` 增量重建（临时索引原地重建后回迁） |
| Milvus `kb_vectors`       | **无变更**                                                                                       | schema 保持 V3 不变，不新增字段、不重建索引                |

### V4 Migrate Runbook

1. 前置：跑 Alembic `0007`（`kb_documents.summary` 列）；跑 `scripts/rag/reindex_es_v4.py` 重建 `doc_summary` 字段（count 对比校验）。
2. 部署 rag-service:v4 容器，4 个 V4 开关默认全 false（=V3 行为，Dockerfile ENV 已设）。
3. 灰度开启：先 `KB_SUMMARY_ENABLED=true` 观察检索命中率/耗时 2h；确认后按需 `KB_SUMMARY_ES_BOOST` 调优（默认 1.0 = 与正文同权）。
4. 摘要数据接入：上传携带 `summary` form 字段，或对存量文档调用 `PUT .../summary` 补全。
5. 回归：全量单测 + `smoke_rag_v4.sh`（上传携带摘要 → 详情返回摘要 → 补全 → 检索返回摘要 → 降级不阻断）。

### 回滚说明

- 全部行为改动以开关隔离：关闭 `KB_SUMMARY_ENABLED` / `KB_ASR_OPENING_TRIM_ENABLED` 即恢复 V3 行为（无状态副作用，无需 DB rollback）。
- PG 列可空、ES 只增字段：回滚零成本、无 schema 回退；`doc_summary` 字段不参与正文 BM25 检索（仅摘要加权），关闭开关后对检索结果零影响。
- 一键回退（docker env / k8s ConfigMap）：

```bash
export KB_SUMMARY_ENABLED=false
export KB_SUMMARY_ES_BOOST=1.0
export KB_ASR_OPENING_TRIM_ENABLED=false
```

### 已知限制（V4，规范覆盖空白，非缺陷）

1. **reindex 后 ES 摘要副本丢失**：`PUT /kb/documents/{doc_id}/reindex` 走"删除旧分块 → 重建分块"路径，不会重写 `doc_summary`，导致 ES 侧摘要副本被清空、PG 侧 `kb_documents.summary` 保留（出现"PG 有、ES 无"）。影响面：仅开关开启时该文档在 ES 路的摘要加权召回弱化；响应 `results[].summary` 始终从 PG 返回，不产生错误数据。恢复方式：对目标文档重新调用 `PUT /kb/documents/{doc_id}/summary` 补全，或对全库重跑 `scripts/rag/reindex_es_v4.py`。
2. **降级入库路径丢弃携带摘要**：入库链路触发降级（embedding 不可用且 `KB_EMBED_DEGRADED_KEEP_TEXT` 开启 / ES 写入失败且 `KB_ES_DEGRADED_KEEP_MILVUS` 开启）时提前返回，携带的 `summary` 被静默丢弃且不记 drop 告警。影响面：仅降级场景下该摘要不生效，正文入库与 degraded 语义不受影响；恢复方式：对目标文档调用 `PUT .../summary` 补全。

## §E V5 Gradio 浏览器前端（独立 compose 服务）

> 来源：`openspec/changes/rag-multimodal-kb-v5/design.md`（D10）与 delta spec `gradio-frontend`。

### 功能说明

- 提供**浏览器可视化入口**（独立进程，端口 7860，**仅内部网络，不映射公网**）：上传页 + 问答页。
- **上传页**：`gr.File`（14 种扩展名白名单 txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma）+ 分类下拉 → 调 `POST /api/v1/kb/documents`（multipart），成功展示 doc_id/status/chunks_count；失败 `gr.Error` 友好提示（错误码/原因），不白屏、不裸 500。
- **问答页**：问题 + top_k + "生成回答"复选 → 调 `POST /api/v1/kb/search`（复选时传 `answer=true`，后端门控 `KB_LLM_ANSWER_ENABLED` 内部判定）→ 渲染"问题 → 召回 chunk（content/score/source/页码 + 图片 `gr.Image` 预览 + 表格 `gr.Dataframe` 预览）→ LLM 回答（`answer` 有值展示，无值展示"回答暂不可用"提示）"。
- **预览链路**：Gradio 与 rag-service **共享挂载 `KB_ASSETS_DIR` 数据卷**（compose 卷 `rag_assets`）——图片按 `kb_doc_assets.storage_path` 相对路径读取渲染，表格按 storage_path 指向的 `content_json`（`{"headers","rows"}`）渲染；**不新增后端静态端点、不改动既有 API**。
- **鉴权**：Gradio 调用 `/kb/*` 时携带 `Authorization: Bearer {RAG_API_TOKEN}`（env 注入，生产由运维配置，**不硬编码进镜像/代码**）；未配置 token 时页面提示"未配置访问凭证"，不发起调用。
- 所有后端调用 try/except + 超时（30s，connect 5s），后端宕机展示友好提示、不卡死。

### 启动方式（docker compose）

```bash
# 1) 注入内部访问 token（生产由运维提供 ADMIN/SUPERVISOR 角色的 JWT；开发可用内部直连 token）
export RAG_API_TOKEN=<your-internal-token>
# 2) 启动（gradio 依赖 rag-service healthy 后启动；rag-service/gradio 共享 rag_assets 卷）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d gradio
```

### 页面地址与访问约束

| 项       | 值                                                                    |
| -------- | --------------------------------------------------------------------- |
| 页面地址 | `http://<host>:7860/`（本地 `http://localhost:7860/`）                |
| 上传接口 | 复用 `POST /api/v1/kb/documents`（不变）                              |
| 检索接口 | 复用 `POST /api/v1/kb/search`（不变）                                 |
| 网络约束 | 仅内部网络（compose 内部 DNS + 宿主机端口），**不映射公网**           |
| 健康检查 | `curl -s -o /dev/null -w "%{http_code}" http://localhost:7860/` = 200 |

### RAG_API_TOKEN 配置说明

- 由 compose 环境注入 `RAG_API_TOKEN`（`docker-compose.yml` 使用 `${RAG_API_TOKEN:?must set internal token}` 强制必填）；Gradio 代码仅从 env 读取，**不落盘、不硬编码**。
- token 语义为后端既有 JWT 鉴权（上传需 ADMIN/SUPERVISOR 角色；检索接口权限 ALL）。未配置时页面提示"后端服务暂不可用：未配置访问凭证（RAG_API_TOKEN）"。
- 开发环境内部网络直连时可注入任意内部 token 绕过（仍走后端既有鉴权校验）。

## §F V5 冒烟测试指引（12 步全链路）

V5 冒烟脚本位于 `scripts/smoke/smoke_rag_v5.sh`，覆盖 12 步全链路（含 Gradio 页面检查）：

|  #  | 步骤                           | 预期结果                                                                                      | 依赖/放行                                                                      |
| :-: | :----------------------------- | :-------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------- |
|  1  | `/health` 探活                 | `healthy`                                                                                     | PG/Milvus/Redis/embedding                                                      |
|  2  | TXT 上传→列表→详情→检索→删除   | 全链路成功（V1 基线回归）                                                                     | 无                                                                             |
|  3  | 上传 `.xlsx`（多 sheet）→ 检索 | 命中具体行，`source=excel`；sheet2 亦命中（`KB_EXCEL_SHEET_ALL=true`）                        | openpyxl（样本生成，缺失 WARN）                                                |
|  4  | 上传 `.pptx`（含备注）→ 检索   | 命中备注内容，`source=ppt`                                                                    | python-pptx（缺失 WARN）                                                       |
|  5  | 上传 `.amr` → 转写入库         | `source=asr`                                                                                  | `KB_ASR_AMR_WMA_ENABLED` + ffmpeg + ASR Provider；未入库 WARN（V4 白名单行为） |
|  6  | 上传损坏音频                   | 业务错误 `code=A3001`，且随后检索正常（worker 不卡死）                                        | 无                                                                             |
|  7  | PPTX 内嵌图片抽取              | 文搜图命中 + `assets[].asset_type=image` + `storage_path`                                     | `KB_IMAGE_EXTRACT_ENABLED` + OCR；不可用 WARN 放行                             |
|  8  | DOCX 内嵌表格抽取              | 单元格命中 + `assets[].asset_type=table` + `storage_path` 指向 `.json`（gr.Dataframe 渲染源） | `KB_TABLE_EXTRACT_ENABLED`；未开启 WARN 放行                                   |
|  9  | 检索 `answer=true`（开关开启） | `answer` 带溯源回答；无 LLM 环境 `answer=null+degraded=true` 降级正确                         | `KB_LLM_ANSWER_ENABLED` + Provider                                             |
| 10  | 检索 `answer=true`（开关关闭） | 响应与 V4 完全一致：`answer=null` + `degraded=false`（零 LLM 调用），V5 契约字段恒存在        | 默认部署（开关全 false）                                                       |
| 11  | 删除含资产文档                 | `kb_doc_assets` 计数 0 + 本地资产目录清理                                                     | `docker exec postgres` / `rag-service` 容器；不可用 WARN                       |
| 12  | Gradio 页面可达性              | `GET http://localhost:7860/` = 200                                                            | gradio 服务 + `RAG_API_TOKEN`；不可达 WARN                                     |

### 运行方式

```bash
# 方式 1：全量冒烟（自动接入 V5 脚本）
bash scripts/smoke/smoke_all.sh

# 方式 2：仅 V5 冒烟
bash scripts/smoke/smoke_rag_v5.sh
```

### 前置条件

- `docker compose up` 启动 rag-service + PG + Milvus + ES + Redis（OCR service、gradio 可选）
- `SMOKE_ADMIN_TOKEN` 环境变量或 conda 环境 `ai_collection` 可用（签发测试 JWT）
- 服务端按需开启 V5 开关（`KB_IMAGE_EXTRACT_ENABLED` / `KB_TABLE_EXTRACT_ENABLED` / `KB_ASR_AMR_WMA_ENABLED` / `KB_LLM_ANSWER_ENABLED`），未开启步骤按 WARN 放行
- 退出码：0=全 PASS（可选组件按 WARN 放行），1=有 FAIL

## §G V5 多模态知识库能力总览

> 来源：`openspec/changes/rag-multimodal-kb-v5/`（spec `document-ingest` / `multimodal-ingest` / `hybrid-search` / `gradio-frontend` / `llm-answer` 与 design.md D1~D11）。实施原则：**增量开发，零副作用**——新能力全部以开关接入（默认全 false = V4 完全行为），不修改 V1~V4 对外接口契约、错误码全集 84 条不新增、Milvus/ES schema 不新增字段不重建索引。

### 功能说明（能力清单）

| 能力                       | 说明                                                                                                                                                  | 开关门控                   | design |
| :------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------- | :----- |
| Excel（.xlsx）入库         | 全部 sheet（`KB_EXCEL_SHEET_ALL`）/ 首行表头（`KB_EXCEL_HEADER_ROW`）/ 行切块序列化 `"sheet名: 列名1:值1,..."` / 损坏或加密 → A3001                   | 无（白名单内格式即启用）   | D2     |
| PPT（.pptx）入库           | 标题+正文+文本框+演讲者备注并入页文本 / 页码与版式占位保守过滤 / 多页按序拼接                                                                         | 无                         | D3     |
| AMR/WMA 音频转写           | ffmpeg 解码 → 16kHz WAV → 复用既有 VAD 裁剪 + ≤60s 分片 + ASR Provider 转写（source=asr）                                                             | `KB_ASR_AMR_WMA_ENABLED`   | D6     |
| 内嵌图片抽取（文搜图）     | PDF（PyMuPDF）/DOCX/PPTX 内嵌图片 → Pillow 缩放（`KB_ASSETS_MAX_SIZE`）→ OCR（ocr-service:8200）→ 文本以 chunk 入库（source=asset_image）+ 资产元数据 | `KB_IMAGE_EXTRACT_ENABLED` | D4     |
| 内嵌表格结构化解析         | PDF（pdfplumber）/DOCX/PPTX 表格保留行列结构、任意单元格命中、`content_json` 结构化预览                                                               | `KB_TABLE_EXTRACT_ENABLED` | D5     |
| 在线大模型回答（LLM 问答） | Provider 抽象（dashscope 默认 / local OpenAI 兼容）+ 检索召回注入 + 溯源引用 + 合规脱敏/前置拦截/后置校验 + 熔断降级                                  | `KB_LLM_ANSWER_ENABLED`    | D8     |
| Gradio 浏览器前端          | 独立 compose 服务（端口 7860）：上传页 + 问答页 + 图片/表格结构化预览（共享 `KB_ASSETS_DIR` 数据卷）                                                  | 独立服务（不依赖开关）     | D10    |

### API 变更表（纯增量，V4 契约零改动）

| 接口                                                 | 变更类型             | 说明                                                                                                                                                                                          |
| :--------------------------------------------------- | :------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `POST /kb/documents`                                 | 扩展名白名单增       | 新增 `.xlsx/.pptx/.amr/.wma`（共 14 种）；白名单外（含 .exe）→ A3001 逻辑不变                                                                                                                 |
| `POST /kb/search`                                    | 请求新增字段         | `answer: bool = false`——`true` 且 `KB_LLM_ANSWER_ENABLED` 时生成回答；`true` 时**跳过查询缓存读取且不写缓存**（其余请求缓存语义与 V4 一致）                                                   |
| `POST /kb/search`                                    | 响应新增字段         | `answer: str \| null`（LLM 回答，降级/未开启恒为 null）；`citations: []`（回答引用的命中文档 doc_id，缺省空数组）；`results[].assets: []`（资产预览，缺省空数组）；`degraded=true` 仅降级场景 |
| 其余接口（DELETE/reindex/sensitive-words/health 等） | **完全保持原有不变** | 路径、请求/响应包、错误码零改动                                                                                                                                                               |

### 数据模型变更

| 存储                        | 变更                                                                                                                                                                                                                                                                                                                                                                                                                                   | 说明                                                       |
| :-------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :--------------------------------------------------------- |
| PostgreSQL `kb_doc_assets`  | **新表**（迁移 `0008_kb_doc_assets.py`，版本号接续 0007）：`id BIGSERIAL PK` / `doc_id UUID FK kb_documents ON DELETE CASCADE` / `asset_type VARCHAR(10) CHECK IN ('image','table')` / `page INT` / `storage_path VARCHAR(500)` / `ocr_text TEXT` / `mime_type VARCHAR(100)` / **`content_json TEXT`（表格行列结构 JSON `{"headers","rows"}`，gr.Dataframe 渲染源；图片为 NULL）** / 公共字段，索引 `(doc_id)`、`(doc_id, asset_type)` | 已同步 step4/01 全局 DDL 单一事实源；ORM `KbDocAsset` 对齐 |
| PostgreSQL `kb_search_logs` | 迁移 0008 增列：`llm_called CHAR(1) DEFAULT '0'` / `llm_degraded CHAR(1) DEFAULT '0'`（VARCHAR+CHECK 对齐类型规范，存量行默认 '0' 不强制回填）                                                                                                                                                                                                                                                                                         | 检索日志记录 LLM 回答调用与降级标记                        |
| Milvus `kb_vectors`         | **无变更**                                                                                                                                                                                                                                                                                                                                                                                                                             | 不新增字段、不重建索引                                     |
| Elasticsearch `kb_bm25`     | **无变更**                                                                                                                                                                                                                                                                                                                                                                                                                             | 资产 OCR 文本以正文 chunk 双索引入库（source=asset_image） |

### 开关清单（14 项）

> 12 项 V5 新增 + `LLM_MODEL`/`LLM_TIMEOUT` 复用既有配置（Query 改写/LLM Provider 共用，不重复定义）。完整说明见 §C「V5 新增开关与配置」。

| 变量                                       | 默认值         | 语义                                           |
| :----------------------------------------- | :------------- | :--------------------------------------------- |
| `KB_LLM_ANSWER_ENABLED`                    | `false`        | LLM 回答总门控（关闭 = 零 LLM 调用，V4 行为）  |
| `LLM_PROVIDER`                             | `dashscope`    | `dashscope` 在线 / `local` 本地 OpenAI 兼容    |
| `LLM_MODEL`                                | `qwen-plus`    | 复用既有（Query 改写/LLM 回答共用）            |
| `LLM_TIMEOUT`                              | 10（本地 5）   | 复用既有，LLM 调用超时                         |
| `LLM_MAX_CONCURRENCY`                      | `4`            | 并发信号量上限，超限排队等待（不降级）         |
| `LLM_LOCAL_BASE_URL` / `LLM_LOCAL_API_KEY` | 空             | local Provider 配置，未配置按不可用降级        |
| `KB_IMAGE_EXTRACT_ENABLED`                 | `false`        | 内嵌图片抽取 + OCR 文搜图                      |
| `KB_TABLE_EXTRACT_ENABLED`                 | `false`        | 内嵌表格结构化解析（content_json 预览）        |
| `KB_EXCEL_SHEET_ALL`                       | `true`         | Excel 全部 sheet 入库（false=仅首个）          |
| `KB_EXCEL_HEADER_ROW`                      | `1`            | Excel 表头行号                                 |
| `KB_ASR_AMR_WMA_ENABLED`                   | `false`        | AMR/WMA ffmpeg 解码转写（关闭=白名单外 A3001） |
| `KB_ASSETS_DIR`                            | `data/assets/` | 资产本地存储目录（与 Gradio 共享 volume）      |
| `KB_ASSETS_MAX_SIZE`                       | `1024`         | 图片渲染最大边长（px，等比缩放）               |

### Gradio 使用与 RAG_API_TOKEN 配置

- 启动/页面地址/访问约束/`RAG_API_TOKEN` 注入见 **§E**（Gradio 浏览器前端）；12 步全链路验证见 **§F**。
- 要点：Gradio 为独立 compose 服务（`docker compose up -d gradio`，依赖 rag-service healthy），`RAG_API_TOKEN` 由环境注入（`${RAG_API_TOKEN:?must set internal token}` 强制必填），前端不硬编码；图片预览按 `storage_path` 从共享 `rag_assets` 卷读取，表格按 `content_json` 渲染 `gr.Dataframe`。

### BREAKING：异步入库与可观测（async-kb-ingest-observability）

**`POST /api/v1/kb/documents` 行为变更（HTTP 200，非 202）：**

| 变更项 | 说明 |
| ------ | ---- |
| 早返回 | 校验通过后立即返回 `{ doc_id, status: "processing" }`；解析/索引在后台完成 |
| 轮询 | 调用方 MUST 轮询 `GET /kb/documents` 或 `GET /kb/documents/{id}` 直至 `status` 为 `active` 或 `failed` |
| 进度字段 | 列表/详情新增 `ingest_stage` / `ingest_progress` / `ingest_error`（failed 时读 error 定位原因） |
| 防重 | 同名且 `status=processing` 或 `status=active` 时重复 POST 返回 B0001，不得并行第二条入库任务 |
| 不可检索 | `processing` / `failed` 文档 chunks 不参与混合检索命中 |
| reindex | 保持同步；默认仅按 PG 正文重切，响应 `assets_rebuilt=false`（不重建 docx/xlsx 内嵌资产）。**表格章节前缀/列汇总增强仅对新上传生效**，存量文档须删除后重传；Gradio reindex 不会补全表格资产 |

**stage → progress 映射：** `parse=25` · `body_index=50` · `asset_index=75` · `done=100`（无资产时跳过 asset_index）。

**配置：** `KB_INGEST_JOB_TIMEOUT`（默认 1800s，后台 job 总时限）；`KB_INGEST_STAGING_DIR`（staging 临时文件目录，任务结束自动清理）。进程重启后 startup 会将超时 `processing` 置 `failed`（`ingest_error` 含 timeout 文案）。

**Gradio：** 上传后立即展示 `processing`；列表含 `stage` 列；reindex 使用 600s 超时 + ReadTimeout 后轮询；ConnectError 仍报「后端不可用」（与 ReadTimeout 区分）。

### V5 Migrate Runbook

1. **数据库**：跑 Alembic `0008_kb_doc_assets.py`（新表 + kb_search_logs 两列）；全局 DDL 单一事实源 step4/01 已收录 `kb_doc_assets`（v1.1），无需二次同步。
2. **CI 双向校验**：`python scripts/rag/check_orm_ddl.py`（DDL ↔ ORM 类型/列/nullable/索引对齐，kb_doc_assets 全维度严格校验，退出码 0=通过）。
3. **部署**：部署 rag-service:v5 容器（新开关 ENV 默认全 false = V4 行为）；`docker compose up -d gradio`（compose base/dev 增量，共享 `rag_assets` 卷 + `RAG_API_TOKEN` 注入）。
4. **灰度开启**：先 `KB_EXCEL_SHEET_ALL/KB_TABLE_EXTRACT_ENABLED` 等无外部依赖能力 → 观察入库/检索 2h；再按需 `KB_IMAGE_EXTRACT_ENABLED`（依赖 OCR）/ `KB_ASR_AMR_WMA_ENABLED`（依赖 ffmpeg+ASR）/ `KB_LLM_ANSWER_ENABLED`（依赖 Provider）。
5. **回归**：全量单测（基线 240 不回退）+ `scripts/smoke/smoke_rag_v5.sh`（12 步，可选组件 WARN 放行）+ 既有 `smoke_rag_v2.sh`（V1~V4 基线不变）。

### 回滚说明

- **行为回滚零成本**：全部能力以开关隔离，关闭 `KB_LLM_ANSWER_ENABLED` / `KB_IMAGE_EXTRACT_ENABLED` / `KB_TABLE_EXTRACT_ENABLED` / `KB_ASR_AMR_WMA_ENABLED` 即恢复 V4 行为（无状态副作用，无需 DB rollback）。
- **数据回滚零成本**：`kb_doc_assets` 为新表（不参与检索主链路）、`kb_search_logs` 新列默认 '0' 可空——schema 无需回退；存量数据不受影响。
- **Gradio 下线**：`docker compose stop gradio` 即可，不参与 rag-service 主链路（页面不可达不影响 API）。
- 一键回退（docker env / k8s ConfigMap）：

```bash
export KB_LLM_ANSWER_ENABLED=false
export KB_IMAGE_EXTRACT_ENABLED=false
export KB_TABLE_EXTRACT_ENABLED=false
export KB_ASR_AMR_WMA_ENABLED=false
```

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

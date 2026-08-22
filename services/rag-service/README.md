# rag-service

RAG 混合检索服务（V2 多模态知识库）。

## §A Redis Key Inventory

### V1 保留（禁止代码级 rename）

| Key | 用途 | TTL |
|-----|------|-----|
| `rag:embed:degraded` | Embedding 熔断标识 | 60s |
| `rag:rerank:degraded` | Rerank 熔断标识 | 60s |
| `rag:cache:query:{md5}` | V1 查询缓存（自然过期，不读写） | 3600s |
| `rag:cache:keys:{category}` | 缓存 key 索引 Set | - |
| `rag:ratelimit:{bucket}:{user}` | 限流计数 | - |

> V1 熔断标识与 step4/03 `<prefix>:<entity>:<id>` 格式存在命名风格冲突（step6/01 强约束熔断 key `{service}:llm_degraded` 兼容），但禁止代码级改 key，避免线上数据丢失。已提交 MR 给全局 step4/03 规范文档补充"rag-service 完整 key 清单"附录。

### V2 新增（严格遵循 step4/03 冒号命名）

| Key | 用途 | TTL |
|-----|------|-----|
| `rag:session:v2:{session_id}` | 多轮会话上下文（LPUSH + LTRIM） | 1800s |
| `rag:cache:query:v2:{md5_with_flags}` | V2 查询缓存（含开关 hash 隔离） | 3600s |
| `rag:ocr:degraded` | OCR 熔断标识 | 60s |
| `rag:asr:degraded` | ASR 熔断标识 | 60s |
| `rag:rewrite:degraded` | Query 改写 LLM 熔断标识 | 60s |
| `rag:sensitive_words:version` | 敏感词版本号（INCR） | - |

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
- Milvus：创建 collection `kb_vectors_v2`（带 version/source），**暂不切 alias**。

### Step 2: Deploy V2 app

- 滚动发布 rag-service:v2 容器。7 个功能开关默认全 false（=V1 行为，Dockerfile ENV 已设）。
- 全量跑 V1 77 用例回归 → 必须 100% 通过（开关全关行为一致）。
- 跑 `scripts/smoke/smoke_rag_v2.sh`（含 OCR PNG 上传 + ASR MP3 上传 + QueryRewrite + Routing + Weighting + RowLevel + Session 灰度 5% 流量）。

### Step 3: Milvus 切换（发布窗口 3:00-3:30）

- 跑 `scripts/rag/rebuild_milvus_v2.py`：旧 collection → v2（带 version=1, source="v1_legacy"）。校验 count 对齐（旧 collection.count == new collection.count）。
- 执行 Milvus alias 切换：`alias "kb_vectors" → collection "kb_vectors_v2"`（原子）。
- 健康检查：`GET /health` 通过（Embedding+Milvus 探活正常）；跑 20 条检索回归，命中溯源 version=1。
- **回滚**：alias 切回旧 collection。V2 应用能读旧 collection schema（缺省 version/source=null）。

### Step 4: ES 切换（紧随 Milvus 切换后）

- 运维切 `ES_READ_INDEX=kb_chunks_bm25`。
- 验证 Step 1 中双写一致（count 对比）。
- **回滚**：切 `ES_READ_INDEX=kb_bm25`。

### Step 5: 灰度开启新能力（发布后 2 天，监控稳定）

7 开关灰度开启时间表模板：

| 时间 | 开关 | 环境变量 | 风险 | 回滚 |
|------|------|----------|------|------|
| Day 2 AM | Query 改写 | `KB_QUERY_REWRITE_ENABLED=true` | 低（confidence 兜底） | 设回 false |
| Day 2 PM | 动态路由 | `KB_ROUTING_ENABLED=true` + 传标准 YAML | 低（无命中=全 1.0） | 设回 false |
| Day 3 AM | 业务加权 | `KB_WEIGHTING_ENABLED=true` | 低（缺省 1.0 行为） | 设回 false |
| Day 3 PM | 行级权限 | `KB_ROW_LEVEL_ENABLED=true` + 传矩阵 YAML | 中（默认兼容全放行） | 设回 false |
| Day 4 AM | 多轮会话 | `KB_MULTITURN_ENABLED=true` | 低（仅影响改写 history） | 设回 false |

每一步开关开启后观察 2h：step6/02 监控项（检索命中率<60%告警 / Embedding 队列积压>100），出现问题立刻关开关。

### Step 6: Post-cleanup（发布 30 天后，观察无异常）

- DROP Milvus 旧 collection。
- ES 删除 kb_bm25 旧 index。
- Alembic 0006（可选）：删除 kb_search_logs 旧 BOOLEAN 列，重命名 tmp 列。

### 双读切换变量清单

| 变量 | 默认值 | 切换效果 |
|------|--------|----------|
| `ES_READ_INDEX` | `kb_bm25`（V1） | 切 `kb_chunks_bm25` 读 V2 索引 |
| `ES_DUAL_WRITE` | `false` | `true` 时写入双索引（过渡期） |
| `KB_QUERY_REWRITE_ENABLED` | `false` | `true` 启用 LLM Query 改写 |
| `KB_ROUTING_ENABLED` | `false` | `true` 启用意图路由加权 |
| `KB_WEIGHTING_ENABLED` | `false` | `true` 启用业务元数据加权 |
| `KB_ROW_LEVEL_ENABLED` | `false` | `true` 启用行级权限过滤 |
| `KB_MULTITURN_ENABLED` | `false` | `true` 启用多轮会话上下文 |
| `KB_EMBED_DEGRADED_KEEP_TEXT` | `false` | `true` 时 Embedding 失败仍写 ES 降级入库 |

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
# Milvus alias 切回旧 collection（见 Step 3 回滚）
```

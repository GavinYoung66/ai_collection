# RAG 多模态知识库 V2 完整交付物梳理

> 文档属性：只读交付梳理（不对应任何可执行改动）
> 变更：`openspec/changes/rag-multimodal-kb-v2`（spec-driven schema）
> 状态：规划工件 done、`openspec validate` valid、冒烟 9/9 PASS

---

## 一、V2 本次迭代新增/变更功能清单

### 1.1 新增功能

| # | 业务能力 | 类型 | spec | tasks | 关键源码 |
| --- | --- | --- | --- | --- | --- |
| F1 | 多模态入库：图片 OCR（PaddleOCR 独立服务）+ 音频 ASR（DashScope/Whisper 双 Provider 工厂）+ Image/Audio Parser | 新增 | multimodal-ingest | §4/5/6 | `app/services/ocr_client.py`、`app/services/asr_providers/factory.py`、`app/parsers/image_parser.py`、`app/parsers/audio_parser.py` |
| F2 | Query 改写链（LLM DashScope，双开关：全局开关 + 入参 rewrite=true 才生效，失败静默回退原文） | 新增 | search-enhancements | §7 | `app/services/query_rewrite_service.py` |
| F3 | 动态路由（intent_routes.yaml 意图→权重，SIGHUP 热重载） | 新增 | smart-retrieval-pipeline | §8 | `app/services/routing_service.py`、`app/main.py L21-35` |
| F4 | 业务加权（WeightingService，RRF 后按 category 权重重排）+ 行级权限（RowAccessService，YAML 矩阵，default 兜底放行） | 新增 | smart-retrieval-pipeline / search-enhancements | §9 | `app/services/weighting_service.py`、`app/services/row_access_service.py` |
| F5 | 多轮会话（SessionService，Redis v2 key，TTL/历史容量/回看） | 新增 | conversational-search | §10 | `app/services/session_service.py` |
| F6 | 敏感词 DB 化：sensitive_words 表 + 50 词 seed + 版本号 + Pub-Sub 热刷新 + refresh/current.dict 接口 | 新增 | sensitive-words-sync | §2.3-2.7、§11 | `app/services/sensitive_words_sync.py`、`app/api/sensitive_words.py` |
| F7 | 检索增强：同 doc_id 去重、RRF 后加权、行权过滤、version/source 溯源、缓存 key 升级 v2（flags_hash 开关隔离） | 新增 | hybrid-search (ADDED) | §12 | `app/services/search_service.py`、`app/services/query_cache.py`、`app/models/search.py` |
| F8 | 7 个功能开关 + OCR/ASR/LLM/session 配置（默认 false=V1 行为） | 新增 | 贯穿各 spec | §1.3-1.5 | `common/config/settings.py L79-108` |

### 1.2 Bug 修复（来自 Goal 代码审查）

| # | 缺陷 | 严重级 | 修复内容 | 对应任务 | 关键位置 |
| --- | --- | --- | --- | --- | --- |
| B1 | 迁移 0005 原 38/54 行直接 DROP+RENAME，违反双写过渡 | P0 | 拆分：0005 仅加 tmp 列+回填，新增 0006_post_cleanup 才 DROP+RENAME | §3.12 | `migrations/versions/0005_rag_service_v2_search_logs_upgrade.py`、`0006_post_cleanup.py` |
| B2 | main.py 无 SIGHUP 热加载（grep=0） | P0 | 注册 `signal.signal(SIGHUP)` + handler 重载路由并记录日志 | §8.5 | `app/main.py L159-162` |
| B3 | startup 只做 Milvus/ES 初始化，未调用敏感词 seed | P0 | `_ensure_sensitive_words_baseline`：count 查询→幂等插入→Redis 版本→count<50 告警 | §11.6 | `app/main.py L38-140` |
| B4 | 闸门 1 阈值维度冲突（0.6 融合分 vs 0.6 稠密相似度） | P1 | 闸门 1 改为"稠密召回最高相似度 <0.6"，dense_hits 空时短路跳过 | §12.6 | `app/services/search_service.py L190-212` |

### 1.3 数据库迁移（3 个新迁移 + 1 个已建表 seed）

| 迁移 | 内容 | 说明 |
| --- | --- | --- |
| `0004_rag_service_v2_docs_meta_and_sensitive_words` | kb_documents +4 列（customer_id/agent_id/call_date/risk_tag）+ sensitive_words 表 + seed 50 词 | `source` 列回填 `v1_legacy`；seed 幂等 `ON CONFLICT` |
| `0005_rag_service_v2_search_logs_upgrade` | kb_search_logs BOOLEAN→VARCHAR(1) 双写过渡：加 tmp 列 + 回填 + 5 新字段（cache_hit/query_original/query_rewritten/intent_tag） | 遵循 Decision 10：7 天观察期 |
| `0006_post_cleanup` | DROP 旧 BOOLEAN 列 + RENAME tmp→正式 + 反向 downgrade | 30 天观察期后执行（README Runbook Step 6） |

### 1.4 运维能力

| # | 能力 | 说明 | 关键位置 |
| --- | --- | --- | --- |
| O1 | SIGHUP 热重载路由 | `kill -HUP <pid>` 无重启热更新 conf/intent_routes.yaml | `app/main.py L21-35` |
| O2 | 幂等 seed + 数量告警 | 重启不重复插入；count<50 打 warning 不阻断 | `app/main.py L38-140` |
| O3 | Milvus/ES schema 重建脚本 | rebuild_milvus_v2.py、reindex_es_v2.py、es_v2_mapping.json | `scripts/rag/` |
| O4 | docker-compose 扩展 + 全局依赖 | ocr-service 等容器扩展；PyYAML/ulid-py/httpx 进全局 requirements | tasks §18 |
| O5 | 限流 + RBAC | 固定窗口计数（Redis INCR+EXPIRE），429/B0006；角色校验 401/B0002、403/B0003 | `app/services/rate_limit.py`、`app/services/require_role.py` |

---

## 二、smoke_rag_v2.sh 完整解读（9 步）

脚本路径：`scripts/smoke/smoke_rag_v2.sh`
前置：docker compose up（rag-service + PG + Milvus + ES + Redis + OCR + mock embed/rerank），脚本自动签发 ADMIN token。

| 步骤 | 做什么 | 调用接口 | 验证什么 |
| --- | --- | --- | --- |
| 1 health | 探测服务存活 | `GET /health` | 响应含 "healthy" |
| 2 上传 TXT | 上传示例文本（V1 路径） | `POST /api/v1/kb/documents` multipart `file=sample_rules.txt category=regulation` | 解析 `data.doc_id` 非空 → V1 上传链路正常 |
| 3 上传 JPG | 生成 1x1 最小 JPG 上传（OCR 路径） | 同上，file=JPG | 有 `ocr_doc_id` 则 PASS；**任何失败一律 WARN+step_pass**（见下） |
| 4 上传 MP3 | 生成最小 MP3 上传（ASR 路径） | 同上，category=case | 有 `asr_doc_id` 则 PASS；**任何失败一律 WARN+step_pass** |
| 5 列表 | 4 种 category 分页查询 | `GET /kb/documents?category=X&page=1&size=5` | `data.total >= 0`（-1 判失败）→ category 过滤可用 |
| 6 检索 V1 | 带 Authorization + X-Session-Id 检索 | `POST /kb/search {"query":"逾期协商还款","category":"regulation","top_k":3}` | `data.results` 数组存在（`len >= 0`）→ V1 检索链路通 |
| 7 检索 V2 | `rewrite=true` 入参重跑 | `POST /kb/search` + rewrite=true | `code=="0"` 或缺失即 PASS → 仅验证入参不报错 |
| 8 删除 | 删除 step2 上传的文档 | `DELETE /kb/documents/{DOC_ID}` | `code=="0"` → PG+Milvus+ES 双索引清理 |
| 9 敏感词 | 触发 refresh + 读字典 | `POST /sensitive-words/refresh` + `GET /sensitive-words/current.dict` | `version >= 1` 且 `dict 行数 >= 50` → DB 版本号 + 数据一致性 |

### WARN 机制说明（step3/4/7）

- **为什么 WARN 但 PASS**：ocr-service 部署（PaddleOCR 镜像）与 DashScope API Key 配置**不在 V2 交付范围**（外部依赖）。脚本设计为"链路观察而非内容验证"：只要请求被受理/失败不导致 500，即视为本迭代代码正常。step7 同理——`rewrite=true` 是否真触发改写由服务端 `KB_QUERY_REWRITE_ENABLED=true` + Key 决定，脚本只验证该入参不会报错（双开关关闭时静默忽略）。
- **什么场景会真正失败**：step3/4 实际上**任何响应都能过**（无 FAIL 分支，WARN 即 PASS）——真正的"失败"体现在后续步骤联动：例如若上传导致服务崩溃/无响应，step5 列表会 `total=-1` FAIL；step7 若返回非 0 错误码（如 A3001）则 FAIL；step9 若 refresh 报错或 dict <50 行则 FAIL。

### 未覆盖场景（诚实清单）

- **回滚/双写过渡**：0005 downgrade、0006 清理、7 天过渡期行为、README Runbook Step 6 均未演练
- **高并发/限流**：429/B0006 触发边界未压测（rate_limit 阈值需自行验证）
- **权限矩阵**：非 ADMIN 调 refresh 的 401/403、各角色×部门行权拦截未覆盖
- **异常入参**：超大/空文件、非法 category、超长 query、top_k 越界、无 Authorization
- **5 种降级熔断**：Milvus down→ES-only、ES down→Milvus-only、双 down→A3005、Rerank down→RRF 排序、Embedding 熔断——冒烟均未模拟（仅单元测试覆盖）
- **功能真实性**：RRF 融合质量、闸门 1/2 阈值边界、改写实际效果、OCR/ASR 真实文本提取、多轮上下文、缓存命中 cache_hit、敏感词命中拦截（B0010）
- **定时任务**：0006 30 天后清理、Pub-Sub 刷新事件消费

> 附注：step6 中计算了 `SESSION_HEADER`（HEAD 请求查 X-Session-Id 透传）但后续未断言该变量，属脚本死代码，不影响通过判定。

---

## 三、源码阅读指引

### 【必须看的核心文件】（按阅读顺序）

| 文件 | 看什么（一句话） |
| --- | --- |
| `app/main.py` | 启动装配：7 开关、SIGHUP 热重载、startup 敏感词 seed（幂等+count<50 告警）、路由/异常/限流注册 |
| `app/services/search_service.py` | 核心编排链：双路召回→RRF 融合→去重→加权→Rerank→行权→双闸门→缓存→溯源，注意 7 开关插入点 |
| `app/api/deps.py` | 鉴权/限流/会话注入/单例缓存（get_routing_service 等，验证 SIGHUP 重载生效） |
| `app/services/routing_service.py` + `app/services/sensitive_words_sync.py` | SIGHUP 重载目标；DB 化敏感词 + 版本号 + Pub-Sub 发布逻辑 |
| `app/vectorstore/milvus_client.py` / `app/vectorstore/es_client.py` | kb_vectors_v2 集合 + alias 自动识别；ES_DUAL_WRITE 双索引读写切换 |
| `app/services/ingest_service.py` | 多模态入库：ParserFactory 分发、source 字段、双索引写入 |
| `common/config/settings.py L79-108` | 7 开关默认 false + 阈值（RAG_SCORE_THRESHOLD=0.6、KB_RERANK_THRESHOLD=0.35 等） |
| `migrations/versions/0004/0005/0006` | DDL 单一事实源；双写过渡与清理的精确 SQL |
| `scripts/smoke/smoke_rag_v2.sh` | 全链路验收口径与 WARN 容忍设计 |

### 【可选阅读】

- 检索增强：`weighting_service.py`、`row_access_service.py`、`session_service.py`、`query_cache.py`、`query_rewrite_service.py`（默认关闭，V1 兼容）
- 多模态：`ocr_client.py`、`asr_client.py`、`asr_providers/*`、`image_parser.py`、`audio_parser.py`（依赖外部服务，未部署）
- API 层：`api/documents.py`、`api/search.py`、`api/sensitive_words.py`、`api/health.py`、`services/rate_limit.py`、`services/require_role.py`
- 数据层：`log_service.py`、`models/orm.py`、`models/search.py`、`repositories/*`
- 运维：`scripts/rag/rebuild_milvus_v2.py`、`reindex_es_v2.py`、`es_v2_mapping.json`
- 测试：`tests/`（重点看 `test_gate_threshold.py`、`test_search_dedup_docid.py`、`test_0004_seed_consistency.py`、`test_sensitive_words_sync.py`）

---

## 四、交付边界

### V2 已完成
- 全部功能代码 + 3 个迁移（0004/0005/0006）+ 8 个 spec 契约章节 + 新测试文件 + 冒烟脚本
- 4 项 P0/P1 缺陷修复；`/opsx-update` 已修正 4 处文档漂移（迁移编号、test 文件名、闸门表述、settings 路径）

### 不在 V2 范围，留给 V3
1. **ocr-service 实际部署**：PaddleOCR 镜像未构建/未启用（代码已就绪，仅缺部署与端到端验证）
2. **DashScope API Key 配置**：Rewrite/ASR 真实效果验证（双开关 + Key 就绪后启用）
3. **剩余 2 条可选文档措辞**（非阻塞）：design.md Decision 10 正文 "Alembic 0004" 编号措辞；hybrid-search spec `source` 字段 "v1_legacy 或 null" 收紧为统一 `v1_legacy`
4. 另有项目级 deferred：LangSmith/OpenInference/wandb 可观测集成、多租户（tenant_id）、GPU 推理性能、0006 清理的定时执行演练

---

## 人工验收检查清单

对照源码逐项核验（只读）：

1. **[开关默认]** `common/config/settings.py`：7 开关默认 `False`，`RAG_SCORE_THRESHOLD=0.6`、`KB_RERANK_THRESHOLD=0.35`、`KB_RETRIEVAL_TOP_N=20`、`KB_RRF_K=60`
2. **[迁移链]** `alembic history`：0001→0002→0003→0004→0005→0006；0005 无 `DROP COLUMN degraded/is_fallback`（非 tmp）与 `RENAME ..._tmp`；0006 才有 DROP+RENAME
3. **[SIGHUP]** main.py 有 `signal.signal(signal.SIGHUP, _hup_handler)`；handler 调 `routing_service.load()` 且 deps 中 `get_routing_service()` 为模块级单例
4. **[seed]** main.py startup 调用 `_ensure_sensitive_words_baseline`：`count<50` 时若 dict 存在走 `INSERT ... ON CONFLICT DO NOTHING`，并 `logger.warning`；Redis `rag:sensitive_words:version` 用 `SET NX`
5. **[闸门 1]** search_service：`max_dense_score = max(h["score"] for h in dense_hits)`；`dense_hits` 为空（ES-only）时短路跳过；`max_dense_score < RAG_SCORE_THRESHOLD` 直接回退兜底文案，不进入 Rerank
6. **[溯源]** 检索响应每条含 `source="v1_legacy"`、`version=1`；同一 doc_id 只出现 1 条（去重后）
7. **[双写]** 开启 `ES_DUAL_WRITE=true` 后上传，`kb_chunks_bm25` 与 `kb_vectors_v2` 均写入；关闭仅 Milvus
8. **[敏感词接口]** `POST /sensitive-words/refresh` 返回 `version` 递增；`GET /sensitive-words/current.dict` ≥50 行；非 ADMIN 调 refresh → 403/B0003
9. **[限流]** 短窗口内连打接口 → 429 + `code=B0006` 统一响应包（阈值见 api 层各路由的 `rate_limiter(bucket, limit)`）
10. **[测试]** `pytest services/rag-service/tests/ -q` 全部通过（V1 77 回归 + V2 新增）；`openspec validate --changes rag-multimodal-kb-v2` 为 valid

> 若清单 10 项全部符合，即可执行 `/opsx-sync` 同步主 spec 后归档。

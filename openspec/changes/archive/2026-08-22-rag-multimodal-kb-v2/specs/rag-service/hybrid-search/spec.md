### Requirement: 双闸门兜底（强制无开关）
系统 MUST 对检索结果执行两层强制阈值判断（对齐 step3/03 不确定性治理 §一.2 和 §三 RAG 置信度行）：
- **闸门 1（全局 L2 检索无果阈值）**：**稠密召回最高相似度（Milvus 向量内积 cosine 相似度，量纲≈0.0–1.0，返回字段 score）< 0.6**（RAG_SCORE_THRESHOLD，step1/01 全局阈值）时，系统判定"知识库语义匹配度不足"，标记 `degraded=true`，直接送入闸门 2 之后的兜底文案分支，**不截取 top_k 返回给 Rerank 结果**。
  - **边界条件说明**：闸门 1 仅在 Milvus 双路召回成功且 `dense_hits` 非空时生效。当 Milvus 连接失败 / Embedding 熔断走 ES-only 降级路径（`dense_hits` 空、`max_dense_score` 为 None）时，**闸门 1 短路跳过**，空语义兜底责任由闸门 2（rerank_score<0.35）单独承担。ES-only 场景下需监控 `kb_search_logs` 中 is_fallback='1' 比例保持 <5%（step3/03 空检索命中率告警阈值），若长期超出则考虑切换到融合分阈值方案（选项 A，apply 代码修复路径新增常量 RAG_FUSED_SCORE_THRESHOLD≈0.005，保障 ES-only 也走闸门 1）。
- **闸门 2（精排低分兜底）**：Top1 rerank_score < **0.35**（KB_RERANK_THRESHOLD）时，返回空 results + 兜底 message + code="0"（业务成功语义）。
任意闸门触发后，`kb_search_logs.is_fallback='1'`，且**严格禁止写入查询缓存**（见本 spec 下方"降级/兜底不写缓存"强制 Requirement）。

#### Scenario: 闸门 1 — Milvus稠密Top1相似度 0.58 < 0.6 判定知识库语义匹配不足
- **WHEN** 用户 query="逾期不还怎么办？"，Milvus dense 向量检索 Top1 的 IP（cosine 内积归一化后）= 0.58 < 0.6
- **THEN** 系统标记 degraded=true，跳过 Rerank 后截取，进入兜底分支：code="0" + message + results=[]，kb_search_logs.is_fallback='1'

#### Scenario: 闸门 2 — Top1 rerank 0.32 < 0.35 触发兜底
- **WHEN** 融合分通过闸门 1（0.78），但 Rerank 精排后 Top1 rerank_score = 0.32
- **THEN** 系统返回 code="0"、results=[]、message="知识库暂无匹配内容，请联系坐席核实"，is_fallback='1'

### Requirement: 降级/兜底响应严格禁止写查询缓存（强制无开关，对齐 PRD §5.2 缓存契约）
系统 MUST 在写查询缓存（`rag:cache:query:v2:*`）前检查本次请求的 `degraded` 与 `is_fallback` 标记；**若 degraded='1' 或 is_fallback='1'，则跳过写缓存步骤**，避免 1 小时 TTL 内缓存"无匹配/降级结果"导致后续知识库更新后仍持续返回旧结果。此规则覆盖 Milvus/ES 单侧宕机、Rerank 宕机、双闸门 1/2 触发、QueryRewrite 置信度降级旁路、Routing 配置旁路等所有会将 degraded 置为 '1' 的场景。

#### Scenario: Milvus 宕机走 ES-only 降级不写缓存
- **WHEN** Milvus 连接失败，检索走 ES-only degraded=true，ES-only 返回 Top1 rerank_score=0.82（非兜底）
- **THEN** 响应返回正常内容 degraded=true，但写缓存切面检测 degraded='1' → DEL 不写入 `rag:cache:query:v2:*`

#### Scenario: 闸门 1 触发兜底不写缓存
- **WHEN** 融合分=0.4 < 0.6，degraded=true、is_fallback='1'
- **THEN** 不写查询缓存；15 分钟后知识库新增对应文档，同 query 再次执行时会直接检索命中（而非读到缓存的兜底空结果）

## MODIFIED Requirements

### Requirement: RRF 融合
系统 MUST 使用 RRF（Reciprocal Rank Fusion）算法融合双路结果：score = 1/(k + rank_dense) + 1/(k + rank_sparse)，k 默认 60，按 chunk_id 去重保留高分；完成后**再按 doc_id 只保留最高分 chunk**（对齐 step4/02 §四.2），同一文档多块仅保留最佳单块。

#### Scenario: 两路检索有重叠结果
- **WHEN** 同一 chunk 在向量检索排第 2、BM25 检索排第 1
- **THEN** 该 chunk 的 RRF score = 1/(60+2) + 1/(60+1) ≈ 0.0337，排名上升

#### Scenario: 同一文档 3 个 chunk 进候选集 → 仅保留最高分
- **WHEN** 同一 doc_id 的 chunk#3（RRF 0.025）、chunk#8（0.018）、chunk#12（0.009）均在融合候选集中
- **THEN** 融合阶段只保留 chunk#3（0.025 最高分）送入 Rerank，最终溯源响应中不会出现同一 doc_id 多条结果

### Requirement: 溯源输出
系统 MUST 在检索结果中附带溯源信息：doc_id、title（文档名）、chunk_index、content（原文片段）、score（RRF 融合分数）、rerank_score、**version**（文档版本号，INT；代码实现默认赋值 version=1，对齐 design Decision 9(a) 重建默认值，同时允许 version=null）、**source**（文档来源，str：v1_legacy / ocr / asr，V1 文档缺省="v1_legacy" 或 null）。

#### Scenario: 检索结果包含溯源
- **WHEN** 用户执行检索并返回结果
- **THEN** 每条结果包含 doc_id、title、chunk_index、content、score、rerank_score 字段

#### Scenario: 新 OCR 文档溯源携带 source=ocr
- **WHEN** 上传制度 JPG 截图（source=ocr）入库后被检索命中
- **THEN** 返回结果每条包含 source="ocr"、version=1（或 V2 新建时默认）；老 V1 文档返回 source=null 或 "v1_legacy"

### Requirement: 降级策略
系统 MUST 支持以下降级场景：Milvus 宕机时降级为 ES-only 检索；ES 宕机时降级为 Milvus-only 检索；双侧宕机返回错误码 A3005；Rerank 宕机时降级 RRF score 排序；Query 改写服务不可用时 bypass 使用原 query；动态路由配置不可用时 bypass 权等权召回。任何降级场景 degraded 标记为 true。

#### Scenario: Milvus 宕机降级
- **WHEN** Milvus 连接失败
- **THEN** 系统仅使用 ES BM25 检索，degraded=true，不返回错误

#### Scenario: 双侧宕机
- **WHEN** Milvus 和 ES 均不可用
- **THEN** 系统返回错误码 A3005，不返回结果

#### Scenario: Query 改写不可用 bypass
- **WHEN** KB_QUERY_REWRITE_ENABLED=true 但 LLM 熔断
- **THEN** 系统 bypass 改写，使用原 query 召回，degraded=true

#### Scenario: 路由配置为空 bypass
- **WHEN** KB_ROUTING_ENABLED=true 但路由 YAML 文件加载为空
- **THEN** 系统 bypass 路由权重，4 个 category 等权召回，degraded=true

## ADDED Requirements

### Requirement: Query 改写前置（开关 KB_QUERY_REWRITE_ENABLED 默认 false）
当开启 KB_QUERY_REWRITE_ENABLED=true 时，系统 SHALL 在 embed 前调用 DashScope LLM 对 query 执行改写并返回 rewritten_query + confidence；confidence<0.6 时**降级使用原 query** 并标 degraded=true。详见同变更下 New Capability `rag-service/smart-retrieval-pipeline` 的 Query 改写规范。关闭时与 V1 行为完全一致（无 LLM 调用无副作用）。

#### Scenario: 开关关闭 bypass
- **WHEN** KB_QUERY_REWRITE_ENABLED=false（默认）
- **THEN** 不调用任何 LLM，检索执行路径等同于 V1（embed → 双路召回 → RRF → Rerank → 兜底）

### Requirement: 动态路由接入（开关 KB_ROUTING_ENABLED 默认 false）
当开启 KB_ROUTING_ENABLED=true 时，系统 SHALL 在双路召回前根据规则意图分类调整 category 召回权重。详见同变更下 New Capability `rag-service/smart-retrieval-pipeline` 的路由规范。关闭时与 V1 行为完全一致。

#### Scenario: 开关关闭 bypass
- **WHEN** KB_ROUTING_ENABLED=false（默认）
- **THEN** 不读取任何路由配置，检索 4 个 category 等权召回，行为等同于 V1

### Requirement: 业务加权接入（开关 KB_WEIGHTING_ENABLED 默认 false）
当开启 KB_WEIGHTING_ENABLED=true 时，系统 SHALL 在 RRF→Rerank 之间叠加文档元数据权重（新通话 ×1.5、高风险 ×1.3）。详见同变更下 New Capability `rag-service/search-enhancements` 的加权规范。关闭时与 V1 行为完全一致，不读文档元数据。

#### Scenario: 开关关闭 bypass
- **WHEN** KB_WEIGHTING_ENABLED=false（默认）
- **THEN** RRF score 原样送入 Rerank，不读取 customer_id/agent_id/call_date/risk_tag 字段

### Requirement: 行级权限接入（开关 KB_ROW_LEVEL_ENABLED 默认 false）
当开启 KB_ROW_LEVEL_ENABLED=true 时，系统 SHALL 在 Rerank 之后截取 top_k 之前按 JWT role/dept 映射的 category 白名单过滤结果。详见同变更下 New Capability `rag-service/search-enhancements` 的权限规范。关闭时与 V1 行为完全一致（不解析 role/dept 字段）。

#### Scenario: 开关关闭 bypass
- **WHEN** KB_ROW_LEVEL_ENABLED=false（默认）
- **THEN** 仅根据请求参数 category 进行过滤，不根据 JWT 角色进行二次过滤

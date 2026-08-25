# RAG Hybrid Search Specification (Delta)

> **禁止触碰范围（V1/V2 已实现能力）**：本变更仅修复 V1/V2 遗留问题。严禁修改、重构、破坏 V1/V2 已落地业务代码、接口、能力与配置；V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10）保持原样；所有新增能力均以开关默认 false 接入，关闭时行为与 V2 完全一致；禁止引入遗留清单之外的新功能、禁止新增错误码、禁止修改对外接口路径与统一响应包 `{"code":"0",message,data,trace_id}`。

## MODIFIED Requirements

### Requirement: 低分兜底策略

系统 MUST 在 Top1 rerank_score < 0.35 时判定无有效资料，不调用 LLM，返回固定兜底文案。兜底响应 results 为空数组，message 字段填写兜底提示。**兜底文案统一为 `"知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。"`**（与代码实现一致；V2 偏差说明 L7 记录"知识库暂无匹配内容，请联系坐席核实"为文档单侧过时表述，本次统一对齐代码文案）。

#### Scenario: 触发兜底

- **WHEN** 检索结果 Top1 的 rerank_score 为 0.2
- **THEN** 返回 results=[]，message="知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。"

#### Scenario: 未触发兜底

- **WHEN** 检索结果 Top1 的 rerank_score 为 0.8
- **THEN** 返回 top_k 条结果，message 为 null

### Requirement: 降级策略

系统 MUST 支持以下降级场景：Milvus 宕机时降级为 ES-only 检索；ES 宕机时降级为 Milvus-only 检索；双侧宕机返回错误码 A3005；Rerank 宕机时降级 RRF score 排序；Query 改写服务不可用时 bypass 使用原 query；动态路由配置不可用时 bypass 权等权召回。任何降级场景 degraded 标记为 true。**熔断探测调用（`rerank.is_degraded()` 等外部依赖探测）MUST 纳入 try/except 容错**：探测本身失败（Redis 不可用等）不向上抛异常，按"未熔断"处理继续主流程，避免 Redis 故障连锁导致检索接口 500（V2 遗留缺陷 19.2）。

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

#### Scenario: 熔断探测 Redis 故障不连锁 500

- **WHEN** `rerank.is_degraded()` 内部因 Redis 连接失败抛异常（Redis 故障）
- **THEN** 探测异常被捕获并按"未熔断"处理，检索主流程继续（degraded 按 V1 规则标记），接口返回正常结果，不返回 HTTP 500

### Requirement: 双闸门兜底（强制无开关）

系统 MUST 对检索结果执行两层强制阈值判断（对齐 step3/03 不确定性治理 §一.2 和 §三 RAG 置信度行）：

- **闸门 1（全局 L2 检索无果阈值）**：**稠密召回最高相似度（Milvus 向量内积 cosine 相似度，量纲≈0.0–1.0，返回字段 score）< 0.6**（RAG_SCORE_THRESHOLD，step1/01 全局阈值）时，系统判定"知识库语义匹配度不足"，直接送入闸门 2 之后的兜底文案分支，**不截取 top_k 返回给 Rerank 结果**，`kb_search_logs.is_fallback='1'`（**degraded 语义说明（D-4 决议·方案A）**：闸门 1 属"无匹配兜底"而非"基础设施降级"，不标记 degraded；degraded 仅表达检索链路基础设施降级（Milvus/ES/Rerank 宕机等），与代码实现一致）。
  - **边界条件说明**：闸门 1 仅在 Milvus 双路召回成功且 `dense_hits` 非空时生效。当 Milvus 连接失败 / Embedding 熔断走 ES-only 降级路径（`dense_hits` 空、`max_dense_score` 为 None）时，**闸门 1 短路跳过**，空语义兜底责任由闸门 2（rerank_score<0.35）单独承担。ES-only 场景下需监控 `kb_search_logs` 中 is_fallback='1' 比例保持 <5%（step3/03 空检索命中率告警阈值），若长期超出则考虑切换到融合分阈值方案（选项 A，apply 代码修复路径新增常量 RAG_FUSED_SCORE_THRESHOLD≈0.005，保障 ES-only 也走闸门 1）。
- **闸门 2（精排低分兜底）**：Top1 rerank_score < **0.35**（KB_RERANK_THRESHOLD）时，返回空 results + 兜底 message + code="0"（业务成功语义）。
  任意闸门触发后，`kb_search_logs.is_fallback='1'`，且**严格禁止写入查询缓存**（见本 spec 下方"降级/兜底不写缓存"强制 Requirement）。

#### Scenario: 闸门 1 — Milvus稠密Top1相似度 0.58 < 0.6 判定知识库语义匹配不足

- **WHEN** 用户 query="逾期不还怎么办？"，Milvus dense 向量检索 Top1 的 IP（cosine 内积归一化后）= 0.58 < 0.6
- **THEN** 系统跳过 Rerank 后截取，进入兜底分支：code="0" + message + results=[]，kb_search_logs.is_fallback='1'（degraded 保持 false，非基础设施降级）

#### Scenario: 闸门 2 — Top1 rerank 0.32 < 0.35 触发兜底

- **WHEN** 融合分通过闸门 1（0.78），但 Rerank 精排后 Top1 rerank_score = 0.32
- **THEN** 系统返回 code="0"、results=[]、message="知识库暂无该业务的明确指引，请查阅相关制度文件或联系合规部。"，is_fallback='1'

---

## 归档备注（仅记录，不改变业务行为）

- **`include_answer` 字段废弃标记（审计 D5）**：`SearchRequest.include_answer` 为 V1-era 遗留死字段，PRD §1.4 明确"不生成话术、不接 LLM（include_answer 已移除）"。本次已删除该字段（`app/models/search.py` 已移除，无其他代码引用），对齐 PRD §1.4。
- **闸门 1 degraded 语义（审计 D1/B3，已决议·方案A）**：V2/V3 期间 spec 曾写闸门 1 触发"标记 degraded=true"，与代码实现存在语义差异（代码纯闸门 1 场景 `degraded=False`、仅 `is_fallback='1'`）。**已按方案A决议：spec 删除闸门 1 的 degraded=true 表述，仅保留 is_fallback='1'**；degraded 语义收敛为"检索链路基础设施降级"（Milvus/ES/Rerank/改写/路由宕机或熔断），与代码实现一致，无需改动业务逻辑。

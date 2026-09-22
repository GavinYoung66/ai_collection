## ADDED Requirements

### Requirement: embed/rerank HTTP 熔断规范化且失败不 500

检索路径上的 Embedding 与 Rerank 是直接 HTTP 调用，MUST 外包共享熔断器，MUST NOT 改走 MCP `call_tool`。熔断 key MUST 为 `rag:mcp_degraded:embed` 与 `rag:mcp_degraded:rerank`；`fail_max=3`，`reset_timeout=60s`。系统 MUST NOT 再写入或查询 `rag:embed:degraded` / `rag:rerank:degraded`。

- Embedding 熔断 open 或调用失败：MUST 返回空向量并走 ES BM25 单路（或既有 ES-only）检索，`degraded=true`，HTTP 200，MUST NOT 500；
- Rerank 熔断 open 或调用失败：MUST 跳过精排，按检索原始分数（RRF score）排序，`degraded=true`，HTTP 200，MUST NOT 500。

闸门 1 在 Embedding 熔断导致 `dense_hits` 为空时 MUST 继续短路跳过（既有 ES-only 边界）。限流 key 前缀 MUST 保持 `rag:ratelimit:{bucket}:{user}`，本变更不改 429 + B0006 契约。

#### Scenario: embed 熔断回退 BM25

- **WHEN** `rag:mcp_degraded:embed` 为 open，用户发起检索
- **THEN** 系统不调向量服务，走 BM25/ES 召回，`degraded=true`，HTTP 200，不是 500；全局键 `rag:embed:degraded` MUST NOT 被 SET

#### Scenario: rerank 熔断用原始分数

- **WHEN** `rag:mcp_degraded:rerank` 为 open，融合候选非空
- **THEN** 系统跳过 rerank-service，按 RRF/原始分数排序返回，`degraded=true`，HTTP 200，不是 500；全局键 `rag:rerank:degraded` MUST NOT 被 SET

#### Scenario: 限流 key 仍带 rag 前缀

- **WHEN** 用户维搜索请求到达且未超限
- **THEN** 计数器 key 形如 `rag:ratelimit:{bucket}:{user}`，行为与迁移前一致

## MODIFIED Requirements

### Requirement: 降级策略

系统 MUST 支持以下降级场景：Milvus 宕机时降级为 ES-only 检索；ES 宕机时降级为 Milvus-only 检索；双侧宕机返回错误码 A3005；Rerank 宕机或熔断 open 时降级 RRF score 排序；Embedding 宕机或熔断 open 时降级 ES BM25；Query 改写服务不可用时 bypass 使用原 query；动态路由配置不可用时 bypass 权等权召回。任何降级场景 degraded 标记为 true。**熔断探测失败（共享熔断器/Redis 不可用等）MUST 纳入容错**：探测本身失败不向上抛异常，按"未熔断"处理继续主流程，避免 Redis 故障连锁导致检索接口 500（V2 遗留缺陷 19.2）。

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

- **WHEN** Rerank/Embedding 熔断探测因 Redis 连接失败抛异常（Redis 故障）
- **THEN** 探测异常被捕获并按"未熔断"处理，检索主流程继续（degraded 按 V1 规则标记），接口返回正常结果，不返回 HTTP 500

### Requirement: Reranker 重排序

系统 SHALL 调用 rerank-service:8101 对 RRF 融合后的候选集进行精排，返回 rerank_score。Rerank 失败或熔断 open 时降级使用 RRF score 排序，degraded 标记为 true。精排 HTTP 调用 MUST 受 `rag:mcp_degraded:rerank` 熔断器保护。

#### Scenario: Rerank 成功

- **WHEN** Reranker 服务正常
- **THEN** 候选集按 rerank_score 重新排序，与原始 RRF score 排序不同

#### Scenario: Rerank 服务不可用降级

- **WHEN** rerank-service 超时或连接失败
- **THEN** 系统使用 RRF score 排序，degraded=true，不阻断检索流程

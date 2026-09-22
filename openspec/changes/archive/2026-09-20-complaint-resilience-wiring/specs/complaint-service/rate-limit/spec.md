## ADDED Requirements

### Requirement: 用 P0 零件拼装五维限流

complaint-service 限流器 SHALL 用 `common.resilience` 零件拼装五个维度：(1) 并发闸用 `Semaphore(max_inflight=20)`；(2) 队列削峰用 `BoundedQueue(max_len=500)`，其 `push` 返回 False 时产出 HTTP `B0006`；(3) 按 MCP server 分桶的令牌用 `rate_limit.allow(key=f"csmp:mcp:{server}", limit=qps, window=1)`，且该维 SHALL 接入 `MCPClient.call` 调用前（超限拒绝且不触达 server）；(4) 动态消费，队列深度 >250 时消费速率 x2、>400 时 x3；(5) 单工单锁用 `idempotency.acquire(key=f"csmp:complaint:judge_lock:{complaint_id}", ttl=1800)`。用户维接入限流 SHALL 接到 judge 路由（`acquire_user`，X-User-Id 优先回退 IP，超限产 `B0006`）。阈值（`max_active=20`、`queue_max=500`）和用户维 key 解析 SHALL 保持不变。MCP server 名以实际注册名为准（`business_ticket` / `call_qc` / `rag_kb` / `data_platform`）。

#### Scenario: 并发闸在 20 封顶

- **WHEN** 50 条投诉并发到达且 20 条已在执行
- **THEN** 剩余 30 条进入 `BoundedQueue`；页面为排队中的投诉展示排队状态

#### Scenario: 队列满返回 B0006 供数据中台重试

- **WHEN** `BoundedQueue`（max_len=500）已满，第 501 条投诉到达
- **THEN** 限流器返回 HTTP `B0006`（不是 500，也不自动转人审），数据中台可退避重试

#### Scenario: 单工单幂等锁

- **WHEN** 同一 `complaint_id` 被并发提交两次
- **THEN** `idempotency.acquire(key=f"csmp:complaint:judge_lock:{complaint_id}")` 对第二次提交返回 False，阻止重复执行

### Requirement: 迁移到零件后 Redis 挂行为不变

Redis 不可用时，限流器 SHALL 静默放行（allow），与迁移前行为一致。用户维 key 解析（X-User-Id 优先、IP 回退）和阈值（`max_active=20`、`queue_max=500`）SHALL 与迁移前实现完全一致。

#### Scenario: Redis 挂仍放行流量

- **WHEN** 请求时 Redis 不可达
- **THEN** 限流器放行请求（不返回 `B0006`）并打 warn 日志；投诉继续推进

#### Scenario: MCP server QPS 超限在调用前拒绝

- **WHEN** 某 MCP server（如 `rag_kb`）在 1s 窗口内调用次数已达配置 qps
- **THEN** `MCPClient.call` 在触达 server 前抛出 MCP 业务错误（调用方按证据缺失降级，不冒 500），零件层发 `csmp.rate_limit.hit` 事件，payload 含 `node`（如 `mcp:rag_kb`）与可用的 `trace_id`

#### Scenario: 用户维接入限流在 judge 路由生效

- **WHEN** 同一用户（X-User-Id 优先、IP 回退）超过接入 QPS 后再次提交研判
- **THEN** 路由返回 `B0006`，且该请求未占用并发槽与单工单幂等锁（不影响后续合法请求）

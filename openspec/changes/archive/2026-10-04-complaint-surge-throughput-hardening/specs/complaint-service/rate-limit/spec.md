## MODIFIED Requirements

### Requirement: 用 P0 零件拼装五维限流

complaint-service 限流器 SHALL 用 `common.resilience` 零件拼装五个维度：(1) 并发闸用 `Semaphore(max_inflight=20)`；(2) 队列削峰用 `BoundedQueue(max_len=500)`，其 `push` 返回 False 时进入队列满分支；(3) 按 MCP server 分桶的令牌限流 SHALL 由 mcp-gateway 的 `MCPRateLimitMiddleware` 统一治理（key `csmp:mcp_ratelimit:{server}`，window=1s，超限返回 HTTP 429 且不触达下游 server）；complaint-service 不重复实现客户端侧 QPS 计数，避免与网关双重计数导致误限流。MCP server 名以实际注册名为准（`business_ticket` / `call_qc` / `rag_kb` / `data_platform`）。(4) 动态消费，队列深度 >250 时消费速率 x2、>400 时 x3；(5) 单工单锁用 `idempotency.acquire(key=f"csmp:complaint:judge_lock:{complaint_id}", ttl=1800)`。用户维接入限流 SHALL 接到 judge 路由（`acquire_user`，X-User-Id 优先回退 IP，超限产 `B0006`）。阈值（`max_active=20`、`queue_max=500`）和用户维 key 解析 SHALL 保持不变。MCP server 名以实际注册名为准（`business_ticket` / `call_qc` / `rag_kb` / `data_platform`）。队列满分支 SHALL 受溢出开关控制：开关关闭（默认）时保持现状产 HTTP `B0006`；开关开启且 Redis 延迟队列未超兜底容量时不产 `B0006`，转入 Redis 延迟队列（见「队列满溢出 Redis 延迟队列与滚动补位」）；仅当溢出开关关闭或延迟队列达到兜底容量上限时才产 HTTP `B0006`。

#### Scenario: 并发闸在 20 封顶

- **WHEN** 50 条投诉并发到达且 20 条已在执行
- **THEN** 剩余 30 条进入 `BoundedQueue`；页面为排队中的投诉展示排队状态

#### Scenario: 队列满返回 B0006 供数据中台重试

- **WHEN** 溢出开关关闭（默认）且 `BoundedQueue`（max_len=500）已满，第 501 条投诉到达
- **THEN** 限流器返回 HTTP `B0006`（不是 500，也不自动转人审），回滚维5幂等锁，数据中台/调用方可退避重试

#### Scenario: 延迟队列达兜底容量仍返回 B0006

- **WHEN** 溢出开关开启，但内存队列与 Redis 延迟队列均达各自兜底容量上限
- **THEN** 限流器返回 HTTP `B0006`，回滚维5幂等锁，不无限堆积

#### Scenario: 单工单幂等锁

- **WHEN** 同一 `complaint_id` 被并发提交两次
- **THEN** `idempotency.acquire(key=f"csmp:complaint:judge_lock:{complaint_id}")` 对第二次提交返回 False，阻止重复执行

## ADDED Requirements

### Requirement: 队列满溢出 Redis 延迟队列与滚动补位

溢出开关开启时，HTTP 研判入口在内存并发闸与 `BoundedQueue` 均满后 SHALL 把 `complaint_id` 溢出写入 Redis 有序 List（FIFO，LPUSH 入 / BRPOP 出），并返回 HTTP 202 已受理，而不是 `B0006`。延迟队列 SHALL 设兜底容量上限，超限才回退 `B0006`。系统 SHALL 在服务生命周期内运行后台补偿循环：滚动 BRPOP 取单后重新申请接入——获槽则执行研判，可入内存队列则随内存队列排队，仍满则带退避塞回延迟队列，禁止无退避空转刷 Redis。补偿循环 SHALL 纳入优雅关停，关停时不丢失已取出未完成的在途工单（重新入队或依赖未完成研判的既有重投/图锁机制）。溢出与补偿全程 SHALL 保证同一 `complaint_id` 最终只研判一次（复用维5幂等锁/图锁/在途守卫）。Redis 不可用时 SHALL 回退到溢出开关关闭时的既有 `B0006` 行为并打 warn，不阻断已获槽工单。

#### Scenario: 队列满溢出到 Redis 并最终补位

- **WHEN** 溢出开关开启，20 条在执行、500 条在内存队列，第 521 条 HTTP 研判请求到达，随后有执行槽释放
- **THEN** 第 521 条返回 202 并进入 Redis 延迟队列，不返回 `B0006`；后台循环在资源释放后将其取出执行，且该工单全程只研判一次

#### Scenario: 仍满时带退避塞回不空转

- **WHEN** 补偿循环取出一条但并发闸与内存队列仍满
- **THEN** 该条带退避被塞回延迟队列，循环不进行无间隔轮询

#### Scenario: 优雅关停不丢在途溢出工单

- **WHEN** 服务在延迟队列仍有积压或补偿循环持有在途工单时收到关停信号
- **THEN** 已受理工单不因关停丢失（重新入队或经既有重投恢复），重启后可继续处理且不重复研判

#### Scenario: Redis 故障回退 B0006

- **WHEN** 溢出开关开启但 Redis 不可达且队列已满
- **THEN** 回退返回 `B0006` 并打 warn，已在执行/排队的工单继续推进不受影响

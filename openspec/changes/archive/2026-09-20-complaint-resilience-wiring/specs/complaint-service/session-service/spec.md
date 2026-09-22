## ADDED Requirements

### Requirement: _judge_with_graph 拆分 GraphInterrupt 与 LLM 错误

`_judge_with_graph` 入口 SHALL 区分 `GraphInterrupt`（正常人审挂起）与真正的 LLM/运行时错误。遇 `GraphInterrupt` 时，session SHALL 推进到 `pending_review` 且不写 `exception_info`。遇 `TimeoutError` / `CircuitBreakerOpenError` / `RuntimeError` 时，session SHALL 转 `pending_review` 且写 `exception_info`（含错误类型）。兜底 `Exception` 处理 SHALL 转 `pending_review` 并写 `unknown_error` 的 exception_info。

#### Scenario: GraphInterrupt 不记录异常

- **WHEN** graph 抛 `GraphInterrupt`（经 `interrupt()` 的人审挂起）
- **THEN** session 状态变为 `pending_review`，`exception_info` 未被设置，不记 LLM 失败日志

#### Scenario: LLM 超时记录异常并转人审

- **WHEN** graph 在 LLM 驱动研判时抛 `TimeoutError`
- **THEN** session 状态变为 `pending_review`，`exception_info.error_type` 为 `timeout`

### Requirement: 两级工单 deadline

session SHALL 强制两级独立 deadline：(1) 排队超时——工单在 `BoundedQueue` 等待超过 10 分钟则弹出转 `pending_review`，code `A5010`；(2) 执行超时——从 `graph.ainvoke` 开始计时，超过 30 分钟触发 `degrade_flow.handle_global_timeout` 转 `pending_review`，code `A5010`。开始时间 SHALL 用 Redis TTL 记录。

#### Scenario: 排队超过 10 分钟转人审

- **WHEN** 工单在 `BoundedQueue` 等待超过 10 分钟仍未获得并发槽
- **THEN** 工单被移出队列，`task_status` 变为 `pending_review`，code `A5010`

#### Scenario: graph 执行超过 30 分钟转人审

- **WHEN** 某工单的 `graph.ainvoke` 已运行超过 30 分钟
- **THEN** `degrade_flow.handle_global_timeout` 触发，session 转 `pending_review`，code `A5010`，部分 state 被保留

#### Scenario: 两级 deadline 相互独立

- **WHEN** 工单仍在排队（队列计时 < 10 分钟）且执行尚未开始
- **THEN** 仅排队超时层级生效；执行超时层级在 `graph.ainvoke` 开始后才启动，两级计时器 SHALL NOT 合并

### Requirement: 韧性事件经 publisher 路由

session SHALL 在启动时把 `common.resilience.hooks.on_event` 接到本服务事件 publisher。韧性事件（限流命中、熔断 open、降级触发）SHALL 以 `csmp.{component}.{action}` 格式命名（如 `csmp.rate_limit.hit`、`csmp.circuit_breaker.open`、`csmp.degrade.triggered`），限流 Redis key 与熔断 key SHALL 带 `csmp:` 服务前缀，使零件按 key 首段派生的事件名符合命名规范。业务降级事件 `csmp.degrade.triggered` payload MUST 携带 `trace_id`、`code`、`reason`、`node`；零件层状态事件（`csmp.circuit_breaker.open` / `csmp.rate_limit.hit`）在有请求上下文时 SHALL 经 context 透传 `trace_id`/`node`，无上下文的纯状态翻转 payload 至少携带 `key`、`reason`（`trace_id`/`node` 可缺省）。publisher 不可用时，hook SHALL 回退到 `structlog`，MUST NOT 抛异常或阻断业务流。

#### Scenario: 降级事件带完整 payload 发出

- **WHEN** 节点降级被触发（如 verdict 节点 LLM 超时）
- **THEN** publisher 收到名为 `csmp.degrade.triggered` 的事件，payload 含 `trace_id`、`code`（如 `A5017`）、`reason`（如 `timeout`）、`node`（如 `verdict`）

#### Scenario: 零件层事件透传请求上下文

- **WHEN** 带 `trace_id` 的 MCP 调用连续失败触发熔断翻转，或命中 MCP/用户维令牌桶
- **THEN** `csmp.circuit_breaker.open` / `csmp.rate_limit.hit` 事件 payload 含 `key`、`reason`，且透传 `node`（如 `mcp:rag_kb`）与本次 `trace_id`

#### Scenario: publisher 不可用不阻断业务

- **WHEN** 事件 publisher 在发出时不可用
- **THEN** hook 经 `structlog` 打 warn 日志后返回，不抛异常；投诉流程不受影响

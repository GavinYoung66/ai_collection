## ADDED Requirements

### Requirement: 韧性 hooks 事件带 qc 前缀且不进六频道白名单

qc-service 启动 lifespan SHALL 调用 `hooks.set_publisher` 注入本服务适配器。shutdown MUST 调用 `hooks.set_publisher(None)`，避免测试间泄漏。韧性事件名 SHALL 为 `qc.rate_limit.hit`、`qc.circuit_breaker.open`、`qc.degrade.triggered`（及零件发出的同前缀变体，如 `qc.rate_limit.degraded`）。有请求上下文时 payload SHALL 携带 `trace_id`（限流路径经 `allow(..., context=)` 透传），并含 `reason` / `key` 等零件字段。适配器 MUST NOT 将这些事件 PUBLISH 到 M1 六个 Redis 频道，MUST NOT 发布 `qc.llm.degraded`。LLM 熔断业务告警 SHALL 仍走既有 `system.alert.raised`（`event=qc:llm_degraded`，`error_code=A2011`）。适配器或下游失败 MUST NOT 抛异常阻断质检主流程。

#### Scenario: 限流命中发出 qc.rate_limit.hit

- **WHEN** raw 限流超限
- **THEN** 钩子发出 `qc.rate_limit.hit`，且 Redis 订阅方收不到新的第七个业务频道

#### Scenario: LLM 熔断仍走 system.alert.raised

- **WHEN** LLM 熔断打开
- **THEN** 仍发布 `system.alert.raised` 且 `event=qc:llm_degraded`、`error_code=A2011`；同时可发出 `qc.circuit_breaker.open` 或 `qc.degrade.triggered` 作为韧性观测，但不使用频道 `qc.llm.degraded`

#### Scenario: shutdown 注销 hooks publisher

- **WHEN** 服务进程关闭或测试 teardown 调用 shutdown
- **THEN** hooks publisher 被置为 None，后续事件回退 structlog，不阻断、不泄漏到下一用例

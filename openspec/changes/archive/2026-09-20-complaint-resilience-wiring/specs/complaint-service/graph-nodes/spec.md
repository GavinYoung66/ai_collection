## ADDED Requirements

### Requirement: LLM 节点在超时或熔断时降级

verdict、fact、reflection、compliance、user_demand、generate_package 节点 SHALL 在 LLM 调用外包 try/except，捕获 `TimeoutError`、`CircuitBreakerOpenError`、`LLMCallError`。捕获后节点 SHALL：(a) 调本服务 `DegradeLLM` 处理该节点场景；(b) 用 `ComplaintRuleEngine` 产出带 `[规则兜底]` 前缀的规则草稿，不得输出空内容；(c) 设 `state.exception_info`，`error_type` ∈ {`llm_degraded`, `timeout`, `circuit_open`}；(d) 设 `state.task_status = pending_review`；(e) 返回降级后的 state，不重新抛异常。

#### Scenario: LLM 超时产出规则草稿并转人审

- **WHEN** verdict 节点的 LLM 调用超时（mock `timeout=0.1s`）
- **THEN** 节点返回带 `[规则兜底]` 前缀的草稿判决，`state.task_status` 为 `pending_review`，`state.exception_info.error_type` 为 `timeout`，无异常冒泡到 graph 运行时

#### Scenario: 熔断 open 产出规则草稿并转人审

- **WHEN** 节点所用模型的 LLM 熔断器 open
- **THEN** 节点返回 `[规则兜底]` 草稿，`state.exception_info.error_type` 为 `circuit_open`，`task_status` 为 `pending_review`，且发出的降级事件携带 code `A5017`

#### Scenario: 节点降级绝不返回空输出

- **WHEN** 6 个节点中任一触发降级路径
- **THEN** 节点的输出字段（verdict / fact / reflection / compliance / user_demand / package）非空且带 `[规则兜底]` 前缀

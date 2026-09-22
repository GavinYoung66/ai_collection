## MODIFIED Requirements

### Requirement: transport 之上的共享可靠性助手

平台 MUST 在 `common` 为外部 LLM HTTP 调用提供共享可靠性助手，与 `common.llm.transport` 组合。助手 MUST 支持有界重试和 Redis（或等效共享）降级 flag 约定。服务本地 provider 调外部 LLM HTTP API 时 MUST 用这些助手做重试/降级，MUST NOT 为同一关注点引入第三条裸 HTTP 客户端路径。平台 MUST 在 `common.llm.reliability` 提供统一入口 `invoke_llm(model, messages, *, fallback_chain=None, timeout=Timeout(), breaker_key=None) -> dict`，编排：选 API Key → 限流检查 → 熔断检查 → 有界重试（仅业务错，超时不重试）→ 失败换 fallback 模型 → 降级。`breaker_key` 未传时，入口 MUST 自动派生为 `{svc}:llm_degraded:{model}`，使熔断器按服务按模型隔离，无需调用方费力。

#### Scenario: 投诉与 RAG 同时遇 LLM HTTP 瞬时故障

- **WHEN** complaint-service 与 rag-service 在故障下调外部 LLM HTTP API
- **THEN** 两者都套用 `common` 的共享重试/降级约定，同时保留各自服务本地编排（路由、schema、引用）

#### Scenario: invoke_llm 编排完整可靠性链

- **WHEN** 某服务调 `invoke_llm(model="deepseek-chat", messages=[...], fallback_chain=["deepseek-chat-backup"])`，主模型因业务错失败
- **THEN** 入口重试（仅业务错），耗尽后换 `fallback_chain` 中的备用模型，再标记降级

#### Scenario: invoke_llm 内超时不重试

- **WHEN** `invoke_llm` 内某次 LLM 调用超时（超过 `Timeout.total`）
- **THEN** 入口不重试该超时尝试，直接走 fallback 或降级，不用重试放大延迟

### Requirement: 降级 key 按服务作用域命名

共享可靠性约定 MUST 允许服务作用域的降级 key（如 rag 与 complaint 用不同前缀），避免一个服务的熔断静默干掉另一个。LLM 降级 key 还 MUST 带模型标识，遵循 `{svc}:llm_degraded:{model}`（如 `rag:llm_degraded:deepseek-chat`、`csmp:llm_degraded:qwen2.5`），使同服务内一个模型的熔断不干掉另一模型。禁止用单全局 key（如 `csmp:llm_degraded=1`）跨所有服务和模型干掉全平台 LLM 调用。既有的全局单实例 `common.llm.circuit_breaker` 熔断器 MUST 被 `common.resilience.breaker` 的按 key 熔断器替换。

#### Scenario: RAG 打开 LLM 降级 flag

- **WHEN** rag-service 在 key `rag:llm_degraded:deepseek-chat` 下标记其 LLM 路径降级
- **THEN** complaint-service 的 `qwen2.5` LLM 路径（key `csmp:llm_degraded:qwen2.5`）不会仅因 rag key 而降级，rag-service 的其他模型也不降级

#### Scenario: 全局单 key 熔断器被拒

- **WHEN** 调用方试图用不带服务前缀或模型标识的熔断 key（如 `llm_degraded`）
- **THEN** 熔断零件拒绝该 key，或 `invoke_llm` 入口自动派生作用域 key；无单 key 干掉全平台

## ADDED Requirements

### Requirement: LLM fallback 链

`invoke_llm` 入口 MUST 接受可选 `fallback_chain: list[str]` 模型名列表。当主模型调用失败（业务错有界重试耗尽后，超时不重试），入口 MUST 尝试链中下一个模型。主模型降级 flag MUST 在主尝试耗尽重试时立即设置，不论 fallback 结果。若链中所有模型都失败，入口 MUST 标记主模型降级 key 并返回/呈现降级结果；MUST NOT 静默返回空或伪造结果。

#### Scenario: 主模型失败，fallback 成功

- **WHEN** 调 `invoke_llm(model="deepseek-chat", fallback_chain=["deepseek-chat-backup", "qwen2.5"])`，`deepseek-chat` 重试后失败，但 `deepseek-chat-backup` 成功
- **THEN** 返回 fallback 结果，且 `deepseek-chat` 降级 flag 被设置（因主失败）；调用方收到有效结果

#### Scenario: 所有 fallback 耗尽后降级

- **WHEN** 主模型与 `fallback_chain` 中所有模型都失败
- **THEN** 入口标记主模型降级 key 并呈现降级结果；不返回伪造空结果

### Requirement: LLM 三级超时

`invoke_llm` 入口 MUST 接受 `timeout: Timeout`（默认 `Timeout(connect=5.0, read=60.0, total=120.0)`），来自 `common.resilience.timeout`。`total` 超时（默认 120s）MUST 大于 MCP `call_tool` 默认 `total`（30s），使挂死的 MCP 调用在编排它的 LLM 调用被杀之前先暴露。

#### Scenario: 慢 LLM 被 total 超时封顶

- **WHEN** LLM 调用超过 `Timeout(total=120.0)`
- **THEN** 调用在 120s 被中止，按超时处理（不重试），入口走 fallback 或降级

#### Scenario: LLM total 超时大于 MCP total

- **WHEN** Agent 在一次 LLM 调用（total=120s）内编排 MCP 调用（total=30s）
- **THEN** LLM total（120s）长于 MCP total（30s），MCP 失败先于 LLM 层被杀暴露

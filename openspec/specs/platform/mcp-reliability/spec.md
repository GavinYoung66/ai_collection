# platform/mcp-reliability

## Purpose

定义共享的 MCP 工具调用可靠性（错误分级、有界重试、按 server 熔断、三级超时），使 complaint-service、qc-service、rag-service 经统一 `call_tool` 入口调 MCP 工具，而非散落会在超时/权限拒绝上重试的 ad-hoc 重试循环。

## Requirements

### Requirement: 统一 MCP call_tool 带错误分级

平台 SHALL 在独立 MCP 网关服务中提供统一 MCP 调用可靠性编排:错误分级 + per-server 熔断 + 三级超时。入口 MUST 将错误分级:(a) 超时——MUST NOT 重试,标记该 server 降级;(b) 权限拒绝——MUST NOT 重试,终止;(c) 业务可重试——重试至多 3 次,指数退避。按 server 熔断器(key `{svc}:mcp_degraded:{server}`)MUST 在连续失败 `fail_max` 次后 open,拒绝后续对该 server 的调用直到 `reset_timeout` 到期。重试 SHALL 单层实现,SHALL NOT 与下游重试叠加。

可靠性编排 SHALL 从 `common/mcp/reliability.py` 迁移到网关服务。Agent 侧 SHALL 保留薄超时(网关 HTTP 调用超时),SHALL NOT 在进程内执行熔断/重试逻辑。网关 SHALL 作为多 Agent 共享的可靠性治理层。

#### Scenario: 超时不重试

- **WHEN** 对 `rag_kb` server 的 MCP 工具调用超时(超过 30s)
- **THEN** 网关不重试,该 server 熔断失败计数递增,记录降级标记;调用方收到超时错误

#### Scenario: 权限拒绝不重试

- **WHEN** MCP 工具调用返回权限拒绝错误
- **THEN** 网关不重试,立即终止;调用方收到权限错误

#### Scenario: 业务可重试错误重试至多 3 次

- **WHEN** MCP 工具调用返回业务可重试错误(如瞬时下游 500)
- **THEN** 网关重试至多 3 次,指数退避;全部失败则熔断失败计数递增

#### Scenario: 按 server 熔断器在连续失败后 open

- **WHEN** `rag_kb` server 在 key `csmp:mcp_degraded:rag_kb` 下累计 `fail_max` 次连续失败
- **THEN** 后续对 `rag_kb` 的调用被熔断器拒绝,不命中该 server,直到 `reset_timeout` 到期

#### Scenario: 一个 server 的熔断不影响另一个

- **WHEN** `rag_kb` server 熔断器在 key `csmp:mcp_degraded:rag_kb` 下 open
- **THEN** 对 `call_qc` server(key `csmp:mcp_degraded:call_qc`)的调用不受影响

#### Scenario: Agent 侧仅保留薄超时

- **WHEN** Agent 调用网关
- **THEN** Agent 侧仅执行网关 HTTP 调用超时,SHALL NOT 在进程内执行熔断/重试逻辑

### Requirement: MCP 超时层级低于 LLM

MCP 网关调用 total 超时(30s)MUST 短于 LLM `invoke_llm` 默认 total 超时(120s),使挂死的 MCP 调用在编排它的 LLM 调用自身 total 超时到期前先暴露。

#### Scenario: MCP total < LLM total

- **WHEN** Agent 在一次 LLM 调用(total=120s)内编排 MCP 调用(total=30s)
- **THEN** MCP 调用在 30s 先超时,让 LLM 层处理 MCP 失败(如降级、fallback)

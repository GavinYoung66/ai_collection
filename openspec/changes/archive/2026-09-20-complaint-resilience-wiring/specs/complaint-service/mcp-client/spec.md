## ADDED Requirements

### Requirement: MCP 客户端走 common.mcp.reliability.call_tool

complaint-service 的 MCP 客户端 `call` 方法 SHALL 委托给 `common.mcp.reliability.call_tool(server, tool, payload, timeout=Timeout(total=30.0))`，不再发原始客户端调用。`breaker_key` 未显式传时 SHALL 自动派生为 `csmp:mcp_degraded:{server}`。超时和权限拒绝 SHALL NOT 重试；业务可重试错误 SHALL 重试至多 3 次。

#### Scenario: 连续失败打开按 server 熔断

- **WHEN** `rag_kb` MCP server 累计 5 次连续失败
- **THEN** key `csmp:mcp_degraded:rag_kb` 下的熔断器 open，后续对 `rag_kb` 的调用被拒绝且不命中该 server，持续 30s 后放 1 个 half-open 探针

#### Scenario: 按 server 熔断隔离

- **WHEN** `rag_kb` 的熔断器在 key `csmp:mcp_degraded:rag_kb` 下 open
- **THEN** 对 `business_ticket` server（key `csmp:mcp_degraded:business_ticket`）的 MCP 调用不受影响，正常进行

#### Scenario: MCP server 熔断触发证据不足降级

- **WHEN** 某 MCP server 熔断器 open，证据采集无法完成
- **THEN** 反思循环标记证据不足，案件转 `pending_review`（不冒 500）

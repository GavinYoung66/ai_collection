## ADDED Requirements

### Requirement: RAG 检索熔断 key 规范化且失败仍出分

qc-service 作为 RAG 客户端调用知识库检索时，熔断/降级标志 MUST 使用 `qc:mcp_degraded:rag_kb`。超时、熔断 open、连接失败或空结果 SHALL 回退规则包关键词 / 已有 hits 并继续打分。系统 MUST NOT 将整单标为失败、MUST NOT 返回 HTTP 500、MUST NOT 仅因检索失败将 `qc_status` 置为 PARTIAL、MUST NOT 发明新错误频道。传输层保持既有 HTTP 检索，MUST NOT 把该客户端改成独立 MCP 网关。

#### Scenario: RAG 熔断仍回退关键词出分

- **WHEN** 检索对端超时或熔断 key `qc:mcp_degraded:rag_kb` 为 open
- **THEN** 管道回退关键词比对，仍给出 AC+规则（及若 LLM 可用则 LLM）分，HTTP 200，不是 500，`qc_status` 不因检索失败变为 PARTIAL

#### Scenario: 检索空结果不整单失败

- **WHEN** kb/search 返回空结果
- **THEN** 与超时相同：回退关键词仍出分，不阻断离线质检

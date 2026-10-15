## Purpose

定义 M1 `call_qc` MCP 数据面（供投诉取证）：仅两个查询工具覆盖质检违规与告警，不实现通话 ASR 或会话主数据工具。

## Requirements

### Requirement: 仅 query_qc_violations 与 query_qc_alerts

qc-service SHALL 在 `call_qc` 下实现 MCP 工具 `query_qc_violations` 与 `query_qc_alerts`。入参 SHALL 为 `(case_id, session_id)`。每行输出 SHALL 含 `qc_record_id`。违规 SHALL 含 `redline_tag`、`violation_rule_id`、`snippet_text` 与时间戳。告警 SHALL 含 `qc_record_id` 与 `alert_id`。

服务 MUST NOT 实现 `query_call_asr` 或 `query_call_sessions` 数据面(归 call-service)。投诉图节点 MUST NOT 直连 qc HTTP;SHALL 经 MCP 网关路由到 qc-service MCP server。本 change MUST NOT 实现 call-service。

qc-service SHALL 在现有 `CallQcServer` 基础上暴露 MCP HTTP endpoint(如 `/mcp` SSE),供网关用 MCP 协议调用。SHALL 保持现有 HTTP API 不变。MCP endpoint SHALL 实现 `list_tools` 和 `call_tool`。工具 schema SHALL 与现有 `TOOL_SPECS` 一致。

#### Scenario: 违规查询返回 qc_record_id

- **WHEN** 以已存在的 case_id 与 session_id 调用 `query_qc_violations`
- **THEN** 每项含 `qc_record_id` 及违规字段

#### Scenario: 无 ASR 工具

- **WHEN** 列出 qc 拥有的 MCP 工具
- **THEN** qc-service 未实现 `query_call_asr` 与 `query_call_sessions`

#### Scenario: MCP endpoint 暴露 list_tools

- **WHEN** 网关用 MCP client 连接 qc-service MCP endpoint 调用 list_tools
- **THEN** 返回 query_qc_violations 和 query_qc_alerts 工具 schema,与 TOOL_SPECS 一致

#### Scenario: MCP endpoint 调用 call_tool

- **WHEN** 网关用 MCP client 调用 call_tool 指定 query_qc_violations
- **THEN** qc-service 返回违规明细列表,行为与现有 CallQcServer.call 一致

#### Scenario: 现有 HTTP API 不变

- **WHEN** 调用 qc-service 现有 HTTP API(如 GET /api/v1/qc/records/{session_id})
- **THEN** 行为与现有实现一致,SHALL NOT 因暴露 MCP endpoint 而改变

### Requirement: 违规查询输出含扣分与制度依据

`query_qc_violations` 的每行输出 SHALL 在现有 `qc_record_id`、`redline_tag`、`violation_rule_id`、`snippet_text` 与时间戳之外，补充本条扣分 `points`、制度条款号 `policy_clause` 与条款原文 `policy_text`（该行无制度映射时允许为空）。MUST NOT 在 MCP 输出中暴露 `grade`。

#### Scenario: 违规行带扣分与条款

- **WHEN** 以已存在 case_id 与 session_id 调用 `query_qc_violations` 且存在 score_eligible 违规
- **THEN** 每项含 `points`、`policy_clause`、`policy_text`，且无 `grade` 字段

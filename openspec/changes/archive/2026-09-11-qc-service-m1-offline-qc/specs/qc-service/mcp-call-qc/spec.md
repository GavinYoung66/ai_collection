## Purpose

定义 M1 `call_qc` MCP 数据面（供投诉取证）：仅两个查询工具覆盖质检违规与告警，不实现通话 ASR 或会话主数据工具。

## ADDED Requirements

### Requirement: 仅 query_qc_violations 与 query_qc_alerts

qc-service SHALL 在 `call_qc` 下实现 MCP 工具 `query_qc_violations` 与 `query_qc_alerts`。入参 SHALL 为 `(case_id, session_id)`。每行输出 SHALL 含 `qc_record_id`。违规 SHALL 含 `redline_tag`、`violation_rule_id`、`snippet_text` 与时间戳。告警 SHALL 含 `qc_record_id` 与 `alert_id`。

服务 MUST NOT 实现 `query_call_asr` 或 `query_call_sessions` 数据面（归 call-service）。投诉图节点 MUST NOT 直连 qc HTTP；SHALL 经 MCP Server（MCP Server MAY 内部 HTTP 调 qc）。本 change MUST NOT 实现 call-service。

#### Scenario: 违规查询返回 qc_record_id

- **WHEN** 以已存在的 case_id 与 session_id 调用 `query_qc_violations`
- **THEN** 每项含 `qc_record_id` 及违规字段

#### Scenario: 无 ASR 工具

- **WHEN** 列出 qc 拥有的 MCP 工具
- **THEN** qc-service 未实现 `query_call_asr` 与 `query_call_sessions`

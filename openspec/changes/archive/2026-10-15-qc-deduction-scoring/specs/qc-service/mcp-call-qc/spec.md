## ADDED Requirements

### Requirement: 违规查询输出含扣分与制度依据

`query_qc_violations` 的每行输出 SHALL 在现有 `qc_record_id`、`redline_tag`、`violation_rule_id`、`snippet_text` 与时间戳之外，补充本条扣分 `points`、制度条款号 `policy_clause` 与条款原文 `policy_text`（该行无制度映射时允许为空）。MUST NOT 在 MCP 输出中暴露 `grade`。

#### Scenario: 违规行带扣分与条款

- **WHEN** 以已存在 case_id 与 session_id 调用 `query_qc_violations` 且存在 score_eligible 违规
- **THEN** 每项含 `points`、`policy_clause`、`policy_text`，且无 `grade` 字段

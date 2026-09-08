## Purpose

为 complaint-service 提供 4 个 MCP Server、共 14 个 tool 的外部进度跟踪与契约对齐文档，对照 DD-05 §4.4 / REQ-CJ-034，不实现真实 MCP Server，也不切换批次11 stub 网关。

## ADDED Requirements

### Requirement: MCP 进度跟踪表覆盖 4 Server × 14 tool

系统 SHALL 提供 MCP Server 进度跟踪表文档，路径为 `docs/complaint-service/external-deps/mcp-server-tracking.md`。跟踪表 MUST 覆盖权威 Server 名 `business_ticket` / `call_qc` / `rag_kb` / `data_platform` 及下列 14 个 tool（PRD §4.4；补齐清单 P1.2 漏列项）：

- `business_ticket`：`query_case_info`、`query_history_tickets`、`query_history_complaints`、`query_user_history_complaints`、`get_complaint_ticket`
- `call_qc`：`query_call_asr`、`query_qc_violations`、`query_call_sessions`
- `rag_kb`：`search_regulations`、`search_response_templates`、`search_complaint_handling_guidelines`
- `data_platform`：`write_session`、`write_human_review_truth`、`push_rag_candidate`

每行 MUST 含列：`server`、`prd_alias`、`tool`、`owner_team`、`status`、`blocker`、`target_date`、`last_updated`。`status` MUST 取自 `not_started` / `in_progress` / `contract_confirmed` / `ready_for_switch` / `blocked`（单元格可为上述枚举或 `TBD`，但列名 MUST 存在）。跟踪表 MUST NOT 把「编码侧已切真实 MCP」标为本批完成条件。

#### Scenario: 跟踪表文件存在

- **WHEN** 检查 `docs/complaint-service/external-deps/mcp-server-tracking.md`
- **THEN** 文件存在且为 Markdown

#### Scenario: 14 个 tool 名称均可检索

- **WHEN** 对跟踪表 grep 14 个权威 tool 名
- **THEN** 每个 tool 名至少出现一次

#### Scenario: 必填列齐全（表头行）

- **WHEN** 检查跟踪表 Markdown 表头行（形如 `| server | prd_alias | ... |`）
- **THEN** 表头行同时含 `server`、`prd_alias`、`tool`、`owner_team`、`status`、`blocker`、`target_date`、`last_updated`（验收用 `^\|` 表头 grep，不得仅靠正文宽匹配）

### Requirement: MCP 契约对齐清单对照 PRD §4.4

系统 SHALL 提供契约对齐清单文档，路径为 `docs/complaint-service/external-deps/mcp-contract-checklist.md`。清单 MUST 为每个 tool 记录：`request_fields`、`response_fields`、`exception_class`（业务类 / 服务故障 / 权限拒绝，对齐 PRD 4.3）、`session_id_required`、`envelope`（统一 `{code, message, data}`）、`pii_masked_at_server`、`mock_aligned`、`confirmed_by`、`confirmed_date`、`gaps`。入参/出参/异常基线 MUST 对齐 DD-05 §4.4；Server 名 MUST 使用下划线权威名，PRD 连字符名仅出现在 `prd_alias`。确认值允许 `TBD`，但列 MUST 齐全。

#### Scenario: 契约清单文件存在

- **WHEN** 检查 `docs/complaint-service/external-deps/mcp-contract-checklist.md`
- **THEN** 文件存在且为 Markdown

#### Scenario: 契约清单列齐全（表头行）

- **WHEN** 检查契约清单 Markdown 表头行（`| ... |`）
- **THEN** 表头行含 `request_fields`、`response_fields`、`exception_class`、`session_id_required`、`envelope`、`pii_masked_at_server`、`mock_aligned`、`confirmed_by`、`confirmed_date`、`gaps`

#### Scenario: session_id 与统一信封被声明

- **WHEN** 检索契约清单中的 `session_id_required` 与 `envelope`
- **THEN** 文档声明全部 tool 必传 `session_id`，且返回信封为 `{code, message, data}`

### Requirement: 本批不切换真实 MCP

系统 SHALL 在 MCP 跟踪/契约文档中明确：本批 MUST NOT 实现 MCP Server，MUST NOT 把批次11 stub 换成真实现，MUST NOT 修改 `config/*.env`。文档 MUST 声明：批次11 pre/prod 已写的 `USE_MOCK_*=false` ≠ 外部 MCP 已就绪；dev/test 与本地联调在外部未就绪前继续走 Mock；切真流量另开变更。

#### Scenario: 文档声明不切真实 MCP

- **WHEN** 检索跟踪表或契约清单中的切换边界声明
- **THEN** 文档含「不实现真实 MCP Server」且含「不切换 stub 网关」类约束（可用 grep 命中 `Mock` 与 `stub`），且含对 `USE_MOCK` 或「不改 env」边界的说明

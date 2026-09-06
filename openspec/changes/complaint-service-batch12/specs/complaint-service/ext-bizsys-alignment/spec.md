## Purpose

为 complaint-service 与传统催收业务系统对齐工单推送、回调接收与复核界面联调提供确认文档，覆盖 REQ-CJ-015/038，本地继续使用 Mock 推送脚本。

## ADDED Requirements

### Requirement: 工单推送接口确认

系统 SHALL 提供业务系统联调确认文档，路径为 `docs/complaint-service/external-deps/bizsys-integration-confirmation.md`。工单推送专节 MUST 对齐 `POST /api/v1/complaints`：必填字段 `complaint_id`、`user_id`、`case_ids`、`complaint_text`、`submit_time`、`source_channel`；幂等按同一 `complaint_id` 不重复建 session。文档 MUST 记录对接方、确认人、确认日期、缺口；本地缓解 MUST 指向 Mock 推送脚本（`scripts/mock/push_complaint.sh`、`scripts/mock/push_batch_complaints.py`）。本批 MUST NOT 修改投诉 API 实现。

#### Scenario: 业务系统确认文档存在

- **WHEN** 检查 `docs/complaint-service/external-deps/bizsys-integration-confirmation.md`
- **THEN** 文件存在且为 Markdown

#### Scenario: 推送字段可检索

- **WHEN** grep 确认文档中的 `POST /api/v1/complaints` 与字段名 `complaint_id`、`case_ids`、`complaint_text`、`source_channel`
- **THEN** 路径与字段名均出现

### Requirement: 回调接收与可靠性确认

系统 SHALL 在同一确认文档中记录传统业务系统侧回调接收约定，对齐 REQ-CJ-038：

- 回调携带 `session_id` + `version`，业务系统按此幂等
- 失败重试 3 次，指数退避 10s / 30s / 60s
- 3 次仍失败入死信 `csmp:callback:dlq`
- 每日对账：`draft_generated` 超 1h 未确认
- 回调 URL、鉴权（含 HMAC 若对接方确认）、确认人、日期、缺口

本批 MUST NOT 修改回调代码；只确认对端是否按上述契约接收。

#### Scenario: 回调可靠性字段可检索

- **WHEN** grep 确认文档中的 `session_id`、`version`、`csmp:callback:dlq`、`10s`、`30s`、`60s`
- **THEN** 幂等键、死信键与三次退避均出现

### Requirement: 复核界面联调确认

系统 SHALL 在同一确认文档中记录复核界面联调：界面由传统业务系统提供，调用 `PUT /api/v1/complaints/{id}/review`。文档 MUST 列出：

- 6 种 `review_action`：`approve` / `modify_verdict` / `modify_response` / `reject_rejudge` / `add_evidence` / `escalate`
- verdict 四值：`agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience` / `pending`（禁止 `normal`）
- 复核界面 10 项必显字段（投诉基本信息、案件信息、证据链、AI 判责草稿、合规意见、用户诉求、答辩初稿、整改建议、Agent 链路、操作区）
- 驳回原因枚举与 `reject_count` 上限 2

本批 MUST NOT 实现或改写复核前端/API。

#### Scenario: 复核动作与四值可检索

- **WHEN** grep 确认文档中的六动作名与四值 verdict
- **THEN** 六个 `review_action` 与四个 verdict 枚举均出现，且文档声明禁止 `normal`

#### Scenario: 十项必显字段被列出

- **WHEN** 检查复核界面专节
- **THEN** 可 grep 到「必显」且含证据链、判责草稿、答辩初稿、操作区等关键项

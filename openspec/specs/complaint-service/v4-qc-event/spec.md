# complaint-service/v4-qc-event

## Purpose

Defines ordered evidence presentation and QC notification events for agent-violation outcomes so quality systems receive deduction rationale, complaint summary, and evidence without operators picking penalty tiers in the review UI.

## Requirements

### Requirement: 违规有序证据清单

当成立结论为坐席违规时，系统 SHALL 向人审展示 AI（或研判路径）产出的证据条目列表，并按文档约定顺序排列（默认按证据时间升序；同秒按类别稳定序）；每条 MUST 含可理解摘要，且 MUST 脱敏后展示。

#### Scenario: 违规可见有序证据

- **WHEN** 人审确认或改判为坐席违规并进入/停留在处置相关视图且存在结构化证据
- **THEN** 详情可见按序排列的证据摘要列表

#### Scenario: 无证据时明示空态

- **WHEN** 人审确认坐席违规但暂无结构化证据
- **THEN** 详情明示空态（非静默空白），不阻塞进入回电处置

### Requirement: 质检事件下发

人审确认坐席违规并完成回电处置步时，系统 SHALL 发布质检事件 **`qc.violation.reported`**，payload MUST 含：`trace_id`、`complaint_id`、扣分/违规原因摘要、用户投诉摘要、有序证据列表（可为内嵌摘要数组，允许空数组）。人审 UI MUST NOT 提供处罚档位选择。实现 MAY 额外发布 `case.risk.upgraded` 以兼容旧链路，但扣分类质检契约以 `qc.violation.reported` 为准。

#### Scenario: 违规确认后发质检事件

- **WHEN** 成立性为坐席违规且回电处置步成功完成
- **THEN** 可观测到 `qc.violation.reported` 且含原因与投诉摘要；响应/页面无处罚档位字段

#### Scenario: 非违规不发扣分质检

- **WHEN** 成立性为用户误解或体验差且处置完成
- **THEN** 系统 MUST NOT 发送 `qc.violation.reported`（回电事件仍须发送）

#### Scenario: 无结构化证据仍可发事件

- **WHEN** 坐席违规但有序证据列表为空且处置完成
- **THEN** 仍发布 `qc.violation.reported`，evidence 为空数组，原因摘要 MUST 非空

### Requirement: pending 补证引导

当 AI 判责为 pending 时，详情 SHALL 提示操作者可重新研判，并引导核对录音、催收电话与短信等原始证据；在成立性未确认前 MUST NOT 完成关单。

#### Scenario: pending 提示补证

- **WHEN** 打开 AI 判责为 pending 的待人审详情
- **THEN** 可见再跑与补证引导，且关单不可用

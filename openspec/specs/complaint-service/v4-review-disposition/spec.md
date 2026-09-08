# complaint-service/v4-review-disposition

## Purpose

Defines stepped human-review disposition for complaint sessions: explicit establishment confirmation, then callback disposition, then close—so operators confirm liability types against evidence rather than blindly approving AI drafts.

## Requirements

### Requirement: 分步人审不可跳步关单

系统 SHALL 将人审处置拆为至少三步且顺序固定：① 成立性确认/改判；② 回电/回访处置；③ 关单。未完成①或②时，关单入口 MUST 禁用或返回业务错误；中间完成后 MUST 可暂停（刷新后仍停留在对应步骤）。

#### Scenario: 未定责不可关单

- **WHEN** 工单处于待人审且尚未完成成立性确认
- **THEN** 用户无法成功关单

#### Scenario: 定责后未回电不可关单

- **WHEN** 成立性已确认但回电/回访事件尚未成功安排
- **THEN** 用户无法成功关单

#### Scenario: 分步可暂停

- **WHEN** 用户完成定责后离开详情再打开
- **THEN** 界面仍处于待处置（回电）步骤，无需重做定责

### Requirement: 成立性显式三选一

人审定责步 SHALL 要求操作者显式选择或确认以下之一：坐席违规（`agent_violation`）、用户误解（`user_misunderstanding`）、合规但体验差（`compliant_but_poor_experience`）。AI 初选结果 MUST 预填且可改；选择 MUST 以证据为基准（定责步 MUST 提供可核对的证据/链路入口或摘要）。提交的成立结论 MUST NOT 为 `pending`；AI 初选为 `pending` 时人审仍可改判为上述三类之一（须改判原因），否则 MUST 拒绝定责并提示再跑或补证。

#### Scenario: 预填并可改判

- **WHEN** AI 初选为用户误解且人审改为坐席违规并提交定责（含改判原因）
- **THEN** 系统保存人审成立结论为坐席违规，并进入处置步（如 `pending_callback`）

#### Scenario: pending 未改判则不可定责

- **WHEN** AI 判责为 pending 且人审未改为三类成立结论之一即提交定责
- **THEN** 定责失败，并提示再跑研判或补证

#### Scenario: pending 可改判为三类后进入处置

- **WHEN** AI 判责为 pending，人审选择坐席违规并填写改判原因后提交
- **THEN** 定责成功并进入处置步

### Requirement: 改判原因

当人审成立结论与 AI 初选不一致时，系统 MUST 要求填写非空改判原因；一致确认时原因可选。

#### Scenario: 改判须填原因

- **WHEN** 人审选择与 AI 初选不同的成立类型并提交
- **THEN** 无改判原因时请求失败或前端阻止提交；有原因时定责成功

### Requirement: 退回 AI 重跑入口

详情 SHALL 提供明确的「退回 AI 重跑」（或等价文案）入口，映射既有 reject_rejudge / 再跑 judge 语义；该入口 MUST NOT 被理解为「驳回用户投诉」。

#### Scenario: 退回文案不歧义

- **WHEN** 用户查看人审操作区
- **THEN** 可见「退回 AI 重跑」类文案，且无「驳回」单独指代用户投诉的歧义主按钮

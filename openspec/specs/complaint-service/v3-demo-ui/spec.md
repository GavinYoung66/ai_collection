# complaint-service/v3-demo-ui Specification

## Purpose

Defines the V3 demo-oriented static frontend interactions so operators can simulate data-platform push, trigger judgment from the list, review in Chinese, and see a lifecycle status strip without using curl.

## Requirements

### Requirement: 模拟数据中台推送按钮

系统 SHALL 在投诉人审列表页提供按钮，文案为「模拟数据中台推送（3 条）」；点击后 MUST 调用演示灌数接口并**新增**恰好 3 条可区分场景的投诉工单（坐席违规 / 用户误解 / 体验差）；每次成功推送的 `complaint_id` MUST 互不与本次及历史冲突（唯一后缀）；成功或失败 MUST 有可见提示并刷新列表。

#### Scenario: 一键推送三条

- **WHEN** 用户点击「模拟数据中台推送（3 条）」且后端可用
- **THEN** 列表新增 3 条工单（id 为新生成），toast/提示成功

#### Scenario: 连续两次推送不冲突

- **WHEN** 用户连续两次成功点击推送
- **THEN** 至少累计 6 条不同 complaint_id（或第二次仍返回 3 个新 id），不得因固定旧 id 导致第二次失败

### Requirement: 点选 pending 触发研判

系统 SHALL 在用户点击列表中 `task_status=pending` 的行时，自动调用 `POST .../judge` 且 `run_graph=true`；研判进行中 MUST 展示加载态并防止重复触发（宜在列表行显示「研判中」或等价禁用）；到达 `pending_review`（或失败终态）后 MUST 打开详情。对非 `pending` 行，点击 MUST 仅打开详情，不得默认重复跑图。

#### Scenario: pending 行触发真图

- **WHEN** 用户点击一条 `pending` 工单
- **THEN** 发起 `run_graph=true` 的 judge，并在完成后进入详情且成功时为 `pending_review`（或文档约定终态）

#### Scenario: 已研判行不重复跑图

- **WHEN** 用户点击 `pending_review` / `resolved` / `closed`（或非 pending）工单
- **THEN** 仅打开详情，不自动再次 judge

### Requirement: 重新研判显式入口

详情页 SHALL 提供中文按钮「重新研判」；仅当用户主动点击且当前状态允许时 MUST 再次 `judge(run_graph=true)`；MUST 有加载与错误提示。`closed` 与 `resolved` MUST 禁用「重新研判」；允许点击的状态限于 `pending` / `pending_review`（及文档约定失败态）。

#### Scenario: 显式重新研判

- **WHEN** 用户在 `pending_review` 详情页点击「重新研判」
- **THEN** 再次触发真图研判并刷新详情展示

#### Scenario: 关单后不可重新研判

- **WHEN** 工单已 `closed`（或 `resolved`）
- **THEN** 「重新研判」不可提交

### Requirement: 中文人审按钮与禁用逻辑

前端 SHALL 将人审主路径展示为分步操作，而非单一模糊「通过 / approve」：定责步使用成立性三选一（坐席违规 / 用户误解 / 体验差）并预填 AI 初选；提供「退回 AI 重跑」映射 reject_rejudge 或再跑 `judge(run_graph=true)`；处置步完成回电安排（触发 `callback.requested`）；关单仅在定责且回电完成后可点。`closed` 后全部禁用。简短语义说明 MUST 写明：定责针对 AI 结论与证据，不是「驳回用户投诉」。

#### Scenario: 通过后可关单

- **WHEN** 用户完成定责与回电处置后点击关单（V4 语义：完成处置闭环，非模糊「通过 AI」）
- **THEN** 状态变为 closed，操作禁用

#### Scenario: 关单后禁用

- **WHEN** 工单已 closed
- **THEN** 定责/回电/关单/退回 AI 均不可再次提交

#### Scenario: 文案不歧义

- **WHEN** 用户查看操作区说明
- **THEN** 「退回 AI 重跑」类文案存在，且不以「驳回」单独表示驳回用户投诉

#### Scenario: 未回电不可关单

- **WHEN** 仅完成定责、尚未完成回电处置
- **THEN** 关单控件禁用或提交失败

### Requirement: 生命周期状态条

详情页 SHALL 展示横向生命周期进度（至少覆盖：接单 → 取证 → 反思 → 三路研判 → 判责 → 答辩 → 待人审 → 已通过 → 已关单），当前段 MUST 与 `task_status` 及/或追踪事件一致（映射规则见 design D4），便于演示讲解。

#### Scenario: 待人审高亮

- **WHEN** 工单处于 `pending_review`
- **THEN** 状态条高亮「待人审」（或等价标签），且前后步骤可读

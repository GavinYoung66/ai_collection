## MODIFIED Requirements

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

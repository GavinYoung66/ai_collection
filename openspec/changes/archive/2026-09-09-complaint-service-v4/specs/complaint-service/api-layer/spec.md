## ADDED Requirements

### Requirement: 人审定责接口

系统 SHALL 提供 `PUT /api/v1/complaints/{id}/disposition/verdict`（路径固定；若实现需别名须在 v4-demo 记载）。请求 MUST 携带成立结论三选一（`agent_violation` | `user_misunderstanding` | `compliant_but_poor_experience`）；与 AI 初选不一致时 MUST 带非空改判原因；成功后工单进入 `pending_callback`（或 design D1 记载等价态）。提交 `pending` 或非法结论 MUST 失败。统一信封与不存在码 A5001 语义保持不变。

#### Scenario: 定责成功

- **WHEN** 对待人审工单提交合法成立结论
- **THEN** code=0，task_status 为 `pending_callback`（或文档等价）

#### Scenario: 改判缺原因失败

- **WHEN** 提交与 AI 初选不同的结论且省略原因
- **THEN** 返回业务错误码（非静默成功）

#### Scenario: pending 结论拒绝

- **WHEN** 定责请求的成立结论为 pending 或未映射到三类之一
- **THEN** 返回业务错误码

### Requirement: 回电处置接口

系统 SHALL 提供 `PUT /api/v1/complaints/{id}/disposition/callback`。成功时 MUST 发布 `callback.requested`（payload 含 `trace_id`、`complaint_id`、成立结论等）。同一工单重复调用 MUST 幂等：返回已安排成功且不重复发布事件。

#### Scenario: 处置发布事件

- **WHEN** 已定责（`pending_callback`）工单首次调用回电处置接口
- **THEN** code=0 且可观测恰好新增一条 callback.requested

#### Scenario: 重复处置幂等

- **WHEN** 已安排回电的工单再次调用回电处置接口
- **THEN** code=0 且不新增重复的 callback.requested

### Requirement: 关单前置校验

`PUT /api/v1/complaints/{id}/close` 在 V4 路径下 MUST 校验：成立性已确认为三类之一且回电已安排（状态允许关单，如 `resolved`）；否则 MUST 失败并说明缺失步骤。

#### Scenario: 缺步关单失败

- **WHEN** 仅完成定责未回电即关单
- **THEN** 关单失败

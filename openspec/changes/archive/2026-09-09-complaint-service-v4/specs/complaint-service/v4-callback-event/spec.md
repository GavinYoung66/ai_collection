## Purpose

Defines the callback.requested event contract so every reviewed complaint schedules a customer callback or revisit via an outbound/agent system, independent of whether the complaint is upheld as an agent violation.

## ADDED Requirements

### Requirement: 凡投诉必回电事件

无论人审成立结论为坐席违规、用户误解或体验差，系统在处置步 SHALL 成功发布 **`callback.requested`** 后才允许进入关单；事件 payload MUST 至少包含：`trace_id`、`complaint_id`、成立结论、回电/回访标记、可选备注与操作者。同一工单重复处置 MUST 幂等（成功已安排、不重复新增事件）。

#### Scenario: 处置成功发布事件

- **WHEN** 人审在处置步确认安排回电/回访且服务健康
- **THEN** publisher（或等价总线）可查询到一条对应 complaint_id 的 `callback.requested` 事件

#### Scenario: 重复处置幂等

- **WHEN** 已成功发布回电事件的工单再次确认回电处置
- **THEN** 不新增第二条 `callback.requested`，接口仍返回成功

#### Scenario: 未发布则不可关单

- **WHEN** 尚未成功发布回电事件
- **THEN** 关单失败或入口禁用
### Requirement: 演示可 mock 下游

系统 MUST 允许在无真实外呼平台时用进程内 mock 订阅/记录消费事件；事件契约与字段 MUST 与生产意图一致，不得因 mock 省略必填字段。

#### Scenario: mock 可观测

- **WHEN** 演示环境发布 callback.requested
- **THEN** 可通过服务提供的事件列表或测试钩子观察到该事件载荷

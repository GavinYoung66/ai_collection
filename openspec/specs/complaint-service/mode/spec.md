## Purpose

定义双模切换服务：single_agent/multi_agent 灰度、一致性达标全量切换、失败回滚 A5014、双签校验。

## Requirements

### Requirement: 双模切换与灰度

系统 SHALL 在 `app/service/mode_service.py` 支持 single_agent / multi_agent 双模式，通过 Redis `csmp:mode:active` + `csmp:mode:gray_percent`（0~100）+ `csmp:mode:gray_sessions` 控制灰度流量。

#### Scenario: 灰度流量分配

- **WHEN** gray_percent=50
- **THEN** 约 50% 新工单走 single_agent，其余走 multi_agent

#### Scenario: 切换失败回滚

- **WHEN** 模式切换写入 Redis 失败
- **THEN** 回滚原模式并返回错误码 **A5014**（非 A5013）

#### Scenario: 路由委托 mode_service

- **WHEN** PUT /api/v1/complaints/mode 合法双签请求
- **THEN** 由 mode_service 写 Redis，路由层不自持第二套模式状态

### Requirement: 一致性达标自动全量

系统 SHALL 在 AI-人工一致率≥85% 且互一致率≥80% 持续达标后自动全量切换至目标模式。

#### Scenario: 达标全量切换

- **WHEN** 一致性指标连续达标
- **THEN** gray_percent 自动置 100

### Requirement: 双签校验

系统 SHALL 要求灰度切换由运营负责人+技术负责人双签确认，缺少任一签则拒绝切换。

#### Scenario: 单签被拒

- **WHEN** 仅运营负责人签署
- **THEN** 拒绝切换

### Requirement: single_agent 单次 LLM

系统 SHALL 在 single_agent 模式下跳过多 Agent 图编排，直接调用单一 LLM 生成全量研判包（Mock Provider 可测，不重写整图）。

#### Scenario: single_agent 单次调用

- **WHEN** 工单在 single_agent 模式下研判
- **THEN** 只调用一次 LLM Provider 生成 verdict+response

# Spec Delta

## ADDED Requirements

### Requirement: 模式切换鉴权与审批身份来源

模式切换端点 MUST 要求具备管理或主管角色。双签 MUST 来自已认证身份（JWT payload）与独立审批记录，MUST NOT 将请求体自报的 `signers` 作为审批依据。未认证请求 MUST NOT 能改变全局模式或发布模式切换事件。

#### Scenario: 匿名请求不能切换模式

- **WHEN** 未认证调用方 `PUT /complaints/mode`
- **THEN** 返回 401/403，模式未改变，不写入全局模式键，不发布切换事件

#### Scenario: 请求体自报双签不能绕过审批

- **WHEN** 未认证或单身份调用方在请求体提交 `signers=["ops","tech"]`
- **THEN** 自报 `signers` 不作为审批依据：未认证请求被拒（401/403）；单身份请求仅登记审批记录，全局模式不变、不发布切换事件，响应返回当前未变的模式，等待第二个不同已认证身份完成审批后切换

#### Scenario: 已认证双身份正常切换

- **WHEN** 已认证的运营与技术负责人分别完成审批后发起切换
- **THEN** 切换成功并留下可追溯的审批记录

### Requirement: 模式状态多副本一致

模式状态 MUST 存储于跨副本共享的存储（Redis）并在服务 lifespan 显式装配。MUST NOT 依赖进程内默认实现，导致多副本状态不一致或重启后丢失。

#### Scenario: 两副本读到同一模式

- **WHEN** 副本 A 将模式切换为 multi_agent 后副本 B 查询当前模式
- **THEN** 副本 B 返回 multi_agent，与副本 A 一致

#### Scenario: 重启后模式保持

- **WHEN** 服务重启后查询当前模式
- **THEN** 返回重启前的有效模式，不回退到进程默认值

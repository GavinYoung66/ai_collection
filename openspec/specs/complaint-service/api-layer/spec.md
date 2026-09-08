## Purpose

定义投诉研判服务的对外 REST API 接口层（模块7）：12 个路由、统一响应包、错误码与路由挂载顺序，对齐 PRD 第 11 章。

## Requirements

### Requirement: 投诉 CRUD 与研判接口

系统 SHALL 在 `app/api/routes/complaints.py` 提供以下路由（前缀 `/api/v1`），Request/Response 对齐 PRD 11.2，统一响应包 `{"code","message","data","trace_id"}`（成功 `code` 为 int `0`，错误为 string 错误码）：

- `POST /complaints`：创建投诉工单
- `GET /complaints`：投诉列表（分页）
- `GET /complaints/{id}`：投诉详情
- `POST /complaints/{id}/judge`：触发研判——**本批默认 stub**（写入 Mock verdict/状态，**不**默认同步执行含 interrupt 的全图）
- `GET /complaints/{id}/status`：查询 task_status
- `GET /complaints/{id}/verdict`：查询判责结果（verdict 四值无 normal）；返回前可走 L4 postcheck
- `GET /complaints/{id}/agent-logs`：查询 Agent 执行日志（脱敏后）
- `PUT /complaints/{id}/review`：提交人工复核（review_action 六值）
- `PUT /complaints/{id}/close`：关闭工单，触发 complaint.closed（stub publisher）

#### Scenario: 创建投诉

- **WHEN** POST /complaints 携带合法 complaint_text
- **THEN** 返回 code=0（int），data 含 complaint_id

#### Scenario: judge 默认 stub 不挂起

- **WHEN** POST /complaints/{id}/judge（默认配置）
- **THEN** 在有限时间内返回 code=0，不因 human_review interrupt 永久挂起

#### Scenario: 查询判责结果四值

- **WHEN** GET /complaints/{id}/verdict 且已有 verdict
- **THEN** verdict ∈ 四值，不含 normal

### Requirement: 统计与一致性接口

系统 SHALL 在 `app/api/routes/stats.py` 提供：
- `GET /complaints/stats`、`GET /complaints/consistency`

#### Scenario: 统计按四值分组且不被 {id} 吞掉

- **WHEN** GET /api/v1/complaints/stats
- **THEN** 返回统计 data.by_verdict 四键，**不是** A5001 工单不存在

### Requirement: 模式切换接口

系统 SHALL 在 `app/api/routes/mode.py` 提供 `PUT /complaints/mode`：
- 写 Redis `csmp:mode:active` + `csmp:mode:gray_percent`
- 失败自动回滚，返回错误码 **A5014**（非 A5013；A5013 保留为样本回流失败）

#### Scenario: 切换失败回滚

- **WHEN** Redis 写入失败
- **THEN** 返回 code="A5014"，原 mode 不变

### Requirement: 统一响应、错误码与挂载顺序

系统 SHALL：
- 所有路由统一响应包；成功 `code: 0`（int），错误 `code: "A50xx"`（str）
- 注册/使用 A5001～A5013（已有）并 **增补 A5014（模式切换失败）**
- `main.py` include 顺序：**stats → mode → complaints**（或等价：固定路径先于 `/{id}`）
- 路由前缀 `/api/v1`

#### Scenario: 挂载后 stats 可访问

- **WHEN** TestClient GET /api/v1/complaints/stats
- **THEN** HTTP 200 且 code=0，by_verdict 齐全

### Requirement: 演示灌数接口（模拟中台推送）

系统 SHALL 提供 `POST /api/v1/complaints/demo/seed`（或文档记载的等价静态路径），一次调用 MUST 创建恰好 3 条可区分场景的投诉工单（对齐 V2 三类场景语义）；每次成功调用 MUST 返回 **3 个新生成的、互不冲突的** `complaint_id`（唯一后缀）；响应 MUST 使用统一信封 `{"code","message","data","trace_id"}`；该接口仅用于本地/演示，MUST 不要求真实数据中台连通。连续多次调用 MUST 持续新增工单，不得因写死旧固定 id 导致第二次失败。

#### Scenario: demo seed 返回三条新 id

- **WHEN** 客户端调用演示灌数接口且服务健康
- **THEN** code=0，data 含 3 个新 complaint_id

#### Scenario: 连续两次 seed

- **WHEN** 连续两次成功调用 demo seed
- **THEN** 两次返回的 id 集合不相交（共 6 个不同 id），或第二次仍为 3 个全新 id

#### Scenario: 路由不被 {id} 吞掉

- **WHEN** 调用 `POST /api/v1/complaints/demo/seed`
- **THEN** 不得返回 A5001「投诉工单不存在」

### Requirement: 研判链路追踪查询接口

系统 SHALL 提供 **`GET /api/v1/complaints/{id}/trace`** 作为 V3 时间线主契约；`data` MUST 含按时间排序的 `events`（或等价）数组，事件来自该工单在研判路径中**已落盘**的追踪存储（非偶发进程内存）；事件字段至少包含类别、名称、时间、耗时或起止、状态摘要；可选返回 `langsmith_url`（或 null）。工单不存在时 MUST 返回既有 A5001 语义。既有 `GET .../agent-logs` 可保留，但不替代本接口语义。

#### Scenario: 查询追踪

- **WHEN** GET `/api/v1/complaints/{id}/trace` 且工单存在并已完成真图研判
- **THEN** code=0，data.events 为数组且至少含 Agent 与 LLM 类事件（有则亦含 MCP/RAG）

#### Scenario: 工单不存在

- **WHEN** GET `/api/v1/complaints/{id}/trace` 且 id 不存在
- **THEN** 返回 A5001（或项目统一不存在码）

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

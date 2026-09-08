## ADDED Requirements

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

## ADDED Requirements

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

## ADDED Requirements

### Requirement: judge 证据包组装与图启动

complaint-service 在 `USE_MOCK_MCP=false` 时，`POST /api/v1/complaints/{complaint_id}/judge` 携带 `run_graph=true` SHALL 成功启动研判图，不返回 500。图 SHALL 按现有节点顺序调用 MCP 工具组装证据包：business_ticket.query_case_info → call_qc.query_call_asr → call_qc.query_qc_violations → rag_kb.search_regulations。每个工具返回值 SHALL 对齐现有 fixture 形状。图 SHALL 停在 `human_review` 节点等待人审，此时 SHALL NOT 已调用 write_session。judge 返回 SHALL 含 `thread_id`（用于后续 review 恢复）。judge 失败 SHALL 返回明确错误信息，不返回 500 静默失败。

#### Scenario: judge 成功启动并停在 human_review

- **WHEN** POST /api/v1/complaints/{complaint_id}/judge 携带 run_graph=true 且 USE_MOCK_MCP=false
- **THEN** 图成功启动，调用 MCP 工具组装证据包，停在 human_review，返回 thread_id，不返回 500

#### Scenario: 证据包工具返回 fixture 形状

- **WHEN** 图节点调用 business_ticket / call_qc / rag_kb 工具
- **THEN** 每个工具返回值形状对齐现有 fixture（非空、字段名一致），图不因形状不符崩溃

#### Scenario: judge 停在人审前不调 write_session

- **WHEN** judge 执行到 human_review 节点
- **THEN** write_session 未被调用，快照投诉列仍为空

### Requirement: 投诉持久化到 PostgreSQL

complaint-service `POST /api/v1/complaints` 创建投诉工单后 SHALL 将记录写入 PostgreSQL `complaints` 表，SHALL NOT 只存内存。创建成功后 SHALL 能通过 `GET /api/v1/complaints/{complaint_id}` 查到。`complaint_id` SHALL 等于请求传入的 `case_id`（现有行为）。重复 POST 同一 `case_id` SHALL NOT 产生第二张工单。创建失败 SHALL 返回明确错误，SHALL NOT 静默成功但未落库。

#### Scenario: 创建后可查到

- **WHEN** POST /api/v1/complaints 携带 complaint_text 和 case_id
- **THEN** PostgreSQL complaints 表有一行，GET /api/v1/complaints/{complaint_id} 返回该记录

#### Scenario: 重复创建不产生第二张

- **WHEN** 同一 case_id 连续两次 POST /api/v1/complaints
- **THEN** complaints 表只有一行，不产生重复

### Requirement: 图状态在 judge 后保持可被 review 恢复

complaint-service judge 创建的图状态 SHALL 在进程内保持到 review 恢复，SHALL NOT 在 judge 请求结束后丢弃。judge SHALL 返回 `thread_id`（= complaint_id）。`PUT /api/v1/complaints/{complaint_id}/review` 携带 `review_action=approve` SHALL 用同一 `thread_id` 恢复图，使图进入已有 `sample_backflow` 节点。SHALL NOT 新增外部 checkpointer 表。

#### Scenario: judge 后图状态保持

- **WHEN** judge 创建图状态并停在 human_review
- **THEN** 图状态在进程内保持，可被 review 用同一 thread_id 恢复

#### Scenario: review 恢复同一线程

- **WHEN** PUT /review 携带 review_action=approve
- **THEN** 用同一 thread_id 恢复图，图进入 sample_backflow

### Requirement: review approve 后触发 write_session

complaint-service 图进入 `sample_backflow` 节点后 SHALL 调用 `write_session` 工具（经 `mcp.call` → data_platform 进程内桥），将研判结论回写到 data-platform 快照 `dw.ads_case_loop` 的投诉列。回写 SHALL 包含 `complaint_id`、`verdict`、`task_status`。回写成功后快照行投诉列 SHALL 非空。回写失败 SHALL 用已有重试（3 次）和 `csmp:backflow:retry`，工单 SHALL NOT 因回写失败变成 500。

#### Scenario: approve 后快照投诉列非空

- **WHEN** review approve 后图进入 sample_backflow 并调 write_session
- **THEN** 快照行投诉列（complaint_id、verdict、task_status）被写入，非空

#### Scenario: 回写失败不阻塞工单

- **WHEN** write_session 连续失败 3 次
- **THEN** 工单状态正常推进，数据入 csmp:backflow:retry，快照行投诉列保持空

## MODIFIED Requirements

### Requirement: 进程内桥分发替代 gateway 转发

`RealMCPTransport.call()` SHALL 为 `business_ticket`、`call_qc`、`data_platform` 三个 server 新增进程内桥分发，替代现有 `_forward_gateway` 分支（现有实现对非 rag_kb 工具一律转发 `MCP_GATEWAY_URL`）。这三个 server SHALL 走进程内桥，SHALL NOT 要求配置 `MCP_GATEWAY_URL`，SHALL NOT 抛 `MCP_GATEWAY_URL missing`。`rag_kb` 仍走已有 `RagKbBridge`。图节点 SHALL 仍只调 `mcp.call`。`USE_MOCK_MCP=false` 时图 SHALL NOT 因桥分发失败而崩溃，SHALL 返回空列表或默认值而非 500。

#### Scenario: 三 server 走进程内桥而非 gateway

- **WHEN** USE_MOCK_MCP=false 且研判图调用 business_ticket / call_qc / data_platform 工具
- **THEN** RealMCPTransport.call 分发到对应进程内桥，不转发 MCP_GATEWAY_URL，不抛 MCP_GATEWAY_URL missing

#### Scenario: 桥分发失败不崩图

- **WHEN** 进程内桥分发时某桥调用失败
- **THEN** 返回空列表或默认值，图继续执行，不返回 500

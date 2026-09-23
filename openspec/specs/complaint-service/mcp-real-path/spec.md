# complaint-service/mcp-real-path

## Purpose

Defines complaint-service behavior for Mock vs real MCP gateway modes, including fail-fast when real mode is selected without a working transport.

## Requirements

### Requirement: Standalone MCP transport is implemented for real mode

When complaint-service is configured for non-mock MCP with standalone (or equivalent remote) gateway mode, the MCP transport MUST be implemented. It MUST NOT raise an unimplemented stub error for supported tool calls used by judgment nodes (`rag_kb` regulation/template tools at minimum, once bridge is configured).

#### Scenario: Real mode tool call

- **WHEN** mock MCP is disabled, standalone/real gateway mode is enabled, and a supported `rag_kb` tool is invoked
- **THEN** the call is dispatched over the real transport path instead of failing with NotImplemented

### Requirement: Mock mode remains default-compatible

With mock MCP enabled, complaint judgment Demo paths that already work MUST continue to function without requiring `rag-service` or a remote MCP gateway.

#### Scenario: Default Demo with mock MCP

- **WHEN** an operator runs complaint-service with mock MCP enabled
- **THEN** MCP tool calls resolve via mock fixtures and the service can become healthy without rag-service

### Requirement: Real mode fails fast when dependencies are missing

When mock MCP is disabled, complaint-service MUST validate that required real-path configuration is present (gateway URL/mode and/or bridge endpoint as applicable). Missing or placeholder configuration MUST fail startup or refuse judgment MCP calls with an explicit configuration/dependency error, not a silent stub success.

#### Scenario: Mock disabled without gateway config

- **WHEN** `USE_MOCK_MCP=false` and required real MCP/bridge settings are missing or placeholders
- **THEN** the service fails fast or returns a clear dependency error on MCP use

### Requirement: Compose dependency semantics match mock vs real

Dev compose for mock MCP complaint demos MUST NOT require `rag-service` solely for health. Real-path deployments MAY depend on MCP gateway/bridge health; they MUST NOT reintroduce Agent-node direct coupling to rag containers as a substitute for MCP.

#### Scenario: Mock compose dependencies

- **WHEN** operators start complaint-service under mock MCP compose overlay
- **THEN** `rag-service` is not a hard `depends_on` requirement for complaint health

### Requirement: 进程内桥分发替代 gateway 转发

`RealMCPTransport.call()` SHALL 为 `business_ticket`、`call_qc`、`data_platform` 三个 server 新增进程内桥分发，替代现有 `_forward_gateway` 分支（现有实现对非 rag_kb 工具一律转发 `MCP_GATEWAY_URL`）。这三个 server SHALL 走进程内桥，SHALL NOT 要求配置 `MCP_GATEWAY_URL`，SHALL NOT 抛 `MCP_GATEWAY_URL missing`。`rag_kb` 仍走已有 `RagKbBridge`。图节点 SHALL 仍只调 `mcp.call`。

#### Scenario: 三 server 走进程内桥而非 gateway

- **WHEN** USE_MOCK_MCP=false 且研判图调用 business_ticket / call_qc / data_platform 工具
- **THEN** RealMCPTransport.call 分发到对应进程内桥，不转发 MCP_GATEWAY_URL，不抛 MCP_GATEWAY_URL missing

### Requirement: business_ticket 进程内桥

complaint-service SHALL 在进程内为研判图调用的 `business_ticket` 工具补真桥，图节点仍只调 `mcp.call`，SHALL NOT 直接 import 其它服务。桥行为：
- `query_case_info`：用 case_id 向 data-platform 快照接口 `GET /api/v1/data/snapshot/{case_id}` 查数据；快照查不到时 fallback 读 `public.cases` 同一行。返回形状对齐现有 fixture（`case_id`、`overdue_amount`、`overdue_days`、`customer_name`、`product_type`、`create_time`；`repayment_records` 允许空数组）。客户名保持脱敏
- `query_history_tickets`：没有历史时 SHALL 返回空列表，SHALL NOT 返回 500，SHALL NOT 为历史新做表
- `query_history_complaints`：没有历史时 SHALL 返回空列表，SHALL NOT 返回 500，SHALL NOT 为历史新做表

#### Scenario: query_case_info 返回脱敏客户名

- **WHEN** 研判图调用 business_ticket.query_case_info 且快照行存在
- **THEN** 返回 fixture 形状，customer_name 为脱敏值

#### Scenario: 历史为空不报错

- **WHEN** query_history_tickets 或 query_history_complaints 查不到历史
- **THEN** 返回空列表，不返回 500

### Requirement: call_qc 进程内桥

complaint-service SHALL 在进程内为研判图调用的 `call_qc` 工具补真桥，图节点仍只调 `mcp.call`。质检服务 SHALL NOT 实现 `call_qc` 工具，SHALL NOT 加到 qc-service。桥行为：
- `query_call_asr`：用 case_id 向 data-platform 快照接口查出通话 session_id，再 GET 已有 qc-source 接口 `GET /api/v1/data/sessions/{session_id}/qc-source`，组装成投诉现有 fixture 的形状：列表，元素含 `call_session_id`、`call_duration`（大于 0）、`asr_text_with_timestamp`（含 speaker 和中文 text）。库内 `dialog_turns.speaker` 只有 `ai|user`；桥把 `ai` 映射为 fixture 里的 `agent`。快照查不到 session_id、或 qc-source 查不到记录，SHALL 返回空列表，SHALL NOT 返回 500。SHALL NOT 改 `evidence_call_qc` 节点
- `query_qc_violations`：图只传 case_id。桥先用快照查出通话 session_id，再 GET 已有 `/api/v1/qc/records/{session_id}`，整理成列表。SHALL NOT 改这个节点，SHALL NOT 给质检新增接口。查不到记录时 SHALL 返回空列表，SHALL NOT 返回 500

#### Scenario: query_call_asr 组装 fixture 形状

- **WHEN** 研判图调用 call_qc.query_call_asr 且快照行有 session_id 且 qc-source 有记录
- **THEN** 返回列表，元素含 call_session_id、call_duration（>0）、asr_text_with_timestamp（含 speaker 和中文 text）

#### Scenario: query_call_asr 查不到返空列表

- **WHEN** 快照查不到 session_id 或 qc-source 查不到记录
- **THEN** 返回空列表，不返回 500

#### Scenario: query_qc_violations 先查 session_id 再查质检记录

- **WHEN** 研判图调用 call_qc.query_qc_violations 传 case_id
- **THEN** 桥先用快照查 session_id，再 GET /api/v1/qc/records/{session_id}，返回列表

#### Scenario: query_qc_violations 查不到返空列表

- **WHEN** 快照查不到 session_id 或 /api/v1/qc/records/{session_id} 查不到记录
- **THEN** 返回空列表，不返回 500

### Requirement: data_platform 回写进程内桥

complaint-service SHALL 在进程内为研判图调用的 `data_platform` 工具（`write_session`、`write_human_review_truth`）补真桥，图节点仍只调 `mcp.call`。现有节点入参只有研判 session_id、task_status、verdict。SHALL NOT 改 `sample_backflow` 节点。桥行为：
- 桥在投诉进程内用研判 session_id 反查 complaint_id 和 case_id，再更新快照 `dw.ads_case_loop` 上的投诉列
- 两个工具入参相同（研判 session_id、task_status、verdict），本闭环里都写入快照的投诉列（complaint_id、研判结论、任务状态）；快照表无独立人审结论列，`write_human_review_truth` 不单独写人审结论，SHALL NOT 为它新增列
- 回写失败 SHALL 继续用投诉已有重试（3 次）和 `csmp:backflow:retry`，工单 SHALL NOT 因回写失败变成 500

#### Scenario: 回写更新快照投诉列

- **WHEN** sample_backflow 节点调用 write_session 且桥反查到 case_id
- **THEN** 快照行的投诉列（complaint_id、研判结论、任务状态）被更新

#### Scenario: 回写失败不阻塞

- **WHEN** 回写连续失败 3 次
- **THEN** 工单状态正常推进，数据入 csmp:backflow:retry，快照行投诉列保持空

### Requirement: USE_MOCK_MCP 进程级开关保持不变

`USE_MOCK_MCP` SHALL 保持为 complaint-service 进程级开关（不是单请求级）。默认值保持 true，现有投诉演示和单测继续走 Mock，SHALL NOT 被本 change 打坏。闭环验收时整个 complaint-service 进程切 `USE_MOCK_MCP=false`。SHALL NOT 实现独立 MCP 网关，SHALL NOT 要求配置 `MCP_GATEWAY_URL`。图节点 SHALL 仍然只调 `mcp.call`，SHALL NOT 直接 import 其它服务，SHALL NOT 直接查 `call_records`。

#### Scenario: 默认 Mock 路径不回归

- **WHEN** USE_MOCK_MCP=true 运行投诉演示和单测
- **THEN** MCP 工具调用走 Mock fixtures，不调真桥

#### Scenario: 闭环切真路径

- **WHEN** USE_MOCK_MCP=false 运行闭环验收
- **THEN** 研判图调用的 business_ticket / call_qc / data_platform 工具走进程内真桥，rag_kb 走已有桥到 rag-service

### Requirement: rag_kb 桥不改

`rag_kb` 桥（`search_regulations`、`search_response_templates`）SHALL 保持现状，打到 rag-service 已有 `POST /api/v1/kb/search`。SHALL NOT 改混合检索、重排、OCR、ASR、分块、Gradio。质检词库 SHALL 继续用已有 `GET /api/v1/sensitive-words/current.dict`。SHALL NOT 把通话录音或案件字段推进知识库。

#### Scenario: rag_kb 桥保持现有行为

- **WHEN** USE_MOCK_MCP=false 时研判图调用 rag_kb.search_regulations
- **THEN** 通过已有桥打到 rag-service POST /api/v1/kb/search，行为与现有 spec 一致

### Requirement: 图未调用的工具不做

图没有调用的工具（含 `push_rag_candidate`、`query_call_sessions`、`get_complaint_ticket`、`search_complaint_handling_guidelines`）SHALL 保持现状，SHALL NOT 新做。

#### Scenario: 未调用工具不新做

- **WHEN** 检查本 change 的实现
- **THEN** 不为图未调用的工具新增任何桥或实现

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

`RealMCPTransport.call()` SHALL 在 `USE_MOCK_MCP=false` 时通过 MCP SDK client 连接独立网关,SHALL NOT 在进程内直接 HTTP 调下游服务。网关 SHALL 负责路由到对应后端。`USE_MOCK_MCP=true` 时 SHALL 走 MockMCPClient,SHALL NOT 连接网关。Graph 节点 SHALL 仍只调 `mcp.call`。`USE_MOCK_MCP=false` 时图 SHALL NOT 因网关调用失败而崩溃,SHALL 返回空列表或默认值而非 500。进程内桥代码(rag_kb_bridge / business_ticket_bridge / call_qc_bridge / data_platform_bridge)SHALL 迁移到网关服务,complaint-service SHALL NOT 保留进程内桥实现。

#### Scenario: 三 server 走进程内桥而非 gateway

- **WHEN** USE_MOCK_MCP=false 且研判图调用 business_ticket / call_qc / data_platform 工具
- **THEN** RealMCPTransport 经 MCP SDK client 调用独立网关,网关路由到对应后端,SHALL NOT 在进程内直接 HTTP 调下游,SHALL NOT 转发 MCP_GATEWAY_URL missing 错误

#### Scenario: 桥分发失败不崩图

- **WHEN** 网关调用下游失败
- **THEN** 返回空列表或默认值,图继续执行,SHALL NOT 返回 500

#### Scenario: 真路径经网关调用

- **WHEN** USE_MOCK_MCP=false 且研判图调用 business_ticket / call_qc / data_platform / rag_kb 工具
- **THEN** RealMCPTransport 经 MCP SDK client 调用网关,网关路由到后端,SHALL NOT 在进程内直接 HTTP 调下游

### Requirement: business_ticket 进程内桥

complaint-service SHALL 在进程内为研判图调用的 `business_ticket` 工具补真桥，图节点仍只调 `mcp.call`，SHALL NOT 直接 import 其它服务。桥行为：
- `query_case_info`：用 case_id 向 data-platform 快照接口 `GET /api/v1/data/snapshot/{case_id}` 查数据；快照查不到时 fallback 读 `public.cases` 同一行。返回形状对齐现有 fixture（`case_id`、`overdue_amount`、`overdue_days`、`customer_name`、`product_type`、`create_time`；`repayment_records` 允许空数组）。桥 SHALL 从快照行的 `product_type`、`repayment_records`、`create_time` 三列读取实际业务值并填入返回，SHALL NOT 在这三字段固定填空串或空列表；仅当快照行对应列确为空（NULL）时才允许返回空值。客户名保持脱敏
- `query_history_tickets`：没有历史时 SHALL 返回空列表，SHALL NOT 返回 500，SHALL NOT 为历史新做表
- `query_history_complaints`：没有历史时 SHALL 返回空列表，SHALL NOT 返回 500，SHALL NOT 为历史新做表

#### Scenario: query_case_info 返回脱敏客户名

- **WHEN** 研判图调用 business_ticket.query_case_info 且快照行存在
- **THEN** 返回 fixture 形状，customer_name 为脱敏值

#### Scenario: query_case_info 读取快照新三列

- **WHEN** 研判图调用 business_ticket.query_case_info 且快照行的 `product_type`/`repayment_records`/`create_time` 三列有业务值
- **THEN** 返回的 `product_type`、`create_time`、`repayment_records` 为快照行的实际业务值，SHALL NOT 是固定空串或空列表

#### Scenario: 快照新三列为空时仍不崩

- **WHEN** 快照行的 `product_type`/`repayment_records`/`create_time` 三列为 NULL
- **THEN** 返回值可为空，但 SHALL NOT 返回 500，图继续执行

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

`USE_MOCK_MCP` SHALL 保持为 complaint-service 进程级开关(不是单请求级)。默认值保持 true,现有投诉演示和单测继续走 Mock,SHALL NOT 被本 change 打坏。闭环验收时整个 complaint-service 进程切 `USE_MOCK_MCP=false`。图节点 SHALL 仍然只调 `mcp.call`,SHALL NOT 直接 import 其它服务。`USE_MOCK_MCP=false` 时 SHALL 连接独立 MCP 网关,SHALL NOT 在进程内嵌桥。

#### Scenario: 默认 Mock 路径不回归

- **WHEN** USE_MOCK_MCP=true 运行投诉演示和单测
- **THEN** MCP 工具调用走 Mock fixtures,不连网关,不调真桥

#### Scenario: 闭环切真路径

- **WHEN** USE_MOCK_MCP=false 运行闭环验收
- **THEN** 研判图调用的 business_ticket / call_qc / data_platform 工具经独立 MCP 网关路由,rag_kb 经网关调 rag-service,全部不进程内嵌桥

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

### Requirement: judge 证据包组装与图启动

complaint-service 在 `USE_MOCK_MCP=false` 时,`POST /api/v1/complaints/{complaint_id}/judge` 携带 `run_graph=true` SHALL 成功启动研判图,不返回 500。图 SHALL 按现有节点顺序经网关调用 MCP 工具组装证据包:business_ticket.query_case_info → call_qc.query_call_asr → call_qc.query_qc_violations → rag_kb.search_regulations。其中 `call_qc.query_call_asr` 由网关内嵌实现(HTTP 调 data-platform qc-source 接口),`call_qc.query_qc_violations` 由网关经 MCP 协议调 qc-service。每个工具返回值 SHALL 对齐现有 fixture 形状。图 SHALL 停在 `human_review` 节点等待人审。judge 返回 SHALL 含 `thread_id`。judge 失败 SHALL 返回明确错误信息,SHALL NOT 返回 500 静默失败。

#### Scenario: judge 成功启动并停在 human_review

- **WHEN** POST /api/v1/complaints/{complaint_id}/judge 携带 run_graph=true 且 USE_MOCK_MCP=false
- **THEN** 图成功启动,经网关调用 MCP 工具组装证据包,停在 human_review,返回 thread_id,不返回 500

#### Scenario: 证据包工具返回 fixture 形状

- **WHEN** 图节点经网关调用 business_ticket / call_qc / rag_kb 工具
- **THEN** 每个工具返回值形状对齐现有 fixture(非空、字段名一致),图不因形状不符崩溃

#### Scenario: judge 停在人审前不调 write_session

- **WHEN** judge 执行到 human_review 节点
- **THEN** write_session 未被调用,快照投诉列仍为空

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

complaint-service 图进入 `sample_backflow` 节点后 SHALL 调用 `write_session` 工具(经 `mcp.call` → 网关 → data_platform 工具实现),将研判结论回写到 data-platform 快照 `dw.ads_case_loop` 的投诉列。回写 SHALL 包含 `complaint_id`、`verdict`、`task_status`。回写成功后快照行投诉列 SHALL 非空。回写失败 SHALL 用已有重试(3 次)和 `csmp:backflow:retry`,工单 SHALL NOT 因回写失败变成 500。

#### Scenario: approve 后快照投诉列非空

- **WHEN** review approve 后图进入 sample_backflow 并经网关调 write_session
- **THEN** 快照行投诉列(complaint_id、verdict、task_status)被写入,非空

#### Scenario: 回写失败不阻塞工单

- **WHEN** write_session 连续失败 3 次
- **THEN** 工单状态正常推进,数据入 csmp:backflow:retry,快照行投诉列保持空

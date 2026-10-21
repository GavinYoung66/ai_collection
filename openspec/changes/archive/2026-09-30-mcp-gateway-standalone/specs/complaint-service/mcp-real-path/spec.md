## MODIFIED Requirements

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

### Requirement: USE_MOCK_MCP 进程级开关保持不变

`USE_MOCK_MCP` SHALL 保持为 complaint-service 进程级开关(不是单请求级)。默认值保持 true,现有投诉演示和单测继续走 Mock,SHALL NOT 被本 change 打坏。闭环验收时整个 complaint-service 进程切 `USE_MOCK_MCP=false`。图节点 SHALL 仍然只调 `mcp.call`,SHALL NOT 直接 import 其它服务。`USE_MOCK_MCP=false` 时 SHALL 连接独立 MCP 网关,SHALL NOT 在进程内嵌桥。

#### Scenario: 默认 Mock 路径不回归

- **WHEN** USE_MOCK_MCP=true 运行投诉演示和单测
- **THEN** MCP 工具调用走 Mock fixtures,不连网关,不调真桥

#### Scenario: 闭环切真路径

- **WHEN** USE_MOCK_MCP=false 运行闭环验收
- **THEN** 研判图调用的 business_ticket / call_qc / data_platform 工具经独立 MCP 网关路由,rag_kb 经网关调 rag-service,全部不进程内嵌桥

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

### Requirement: review approve 后触发 write_session

complaint-service 图进入 `sample_backflow` 节点后 SHALL 调用 `write_session` 工具(经 `mcp.call` → 网关 → data_platform 工具实现),将研判结论回写到 data-platform 快照 `dw.ads_case_loop` 的投诉列。回写 SHALL 包含 `complaint_id`、`verdict`、`task_status`。回写成功后快照行投诉列 SHALL 非空。回写失败 SHALL 用已有重试(3 次)和 `csmp:backflow:retry`,工单 SHALL NOT 因回写失败变成 500。

#### Scenario: approve 后快照投诉列非空

- **WHEN** review approve 后图进入 sample_backflow 并经网关调 write_session
- **THEN** 快照行投诉列(complaint_id、verdict、task_status)被写入,非空

#### Scenario: 回写失败不阻塞工单

- **WHEN** write_session 连续失败 3 次
- **THEN** 工单状态正常推进,数据入 csmp:backflow:retry,快照行投诉列保持空

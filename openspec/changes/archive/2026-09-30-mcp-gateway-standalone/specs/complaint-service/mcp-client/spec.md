## MODIFIED Requirements

### Requirement: MCP Client 统一调用封装

系统 SHALL 在 `app/mcp/client.py` 提供 `MCPClient`,核心方法 `async call(server: str, tool: str, params: dict, *, trace_id: str, session_id: str) -> dict`,具备:
- 网关调用超时控制(薄超时,Agent 侧快速失败)
- PII 脱敏二次校验(defense-in-depth):复用 `common.security.mask`,检测到明文 PII 则脱敏并告警
- 调用审计:每次调用记录 server/tool/params 摘要/耗时/状态/错误信息,结构对齐 `complaint_agent_logs.mcp_calls`(JSONB)
- 异常类型:`MCPTimeoutError`、`MCPError`(优先复用批次0 `tests/mock/mcp/errors.py`;`app/mcp/errors.py` 可 re-export,禁止两套不兼容异常类)
- Server 名与 tool 清单对齐 PRD 4.4 / Mock `SERVERS`:`business_ticket` / `call_qc` / `rag_kb` / `data_platform`;禁止使用 `case_server` 等别名
- `USE_MOCK_MCP=true` 时 SHALL 走 `MockMCPClient`,SHALL NOT 连接网关
- `USE_MOCK_MCP=false` 时 SHALL 走 MCP SDK client 连接网关 MCP server,SHALL NOT 在进程内直接 HTTP 调下游服务

可靠性编排(熔断/重试/超时/限流)SHALL 由网关承接,Agent 侧 SHALL 保留薄超时(网关调用超时)和 PII 二次校验。Graph 节点 SHALL 仍调 `mcp.call`,SHALL NOT 感知 transport 变化。

【REQ-CJ-002】禁止直连传统业务系统数据库或内部 HTTP 接口,所有外部能力只走 MCP 网关。

#### Scenario: call 可对 Mock Server 返回 dict

- **WHEN** `MCPClient.call("business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="t1", session_id="s1")` 在 Mock MCP Server 环境下执行
- **THEN** 返回 `dict`,含预设案件数据,不抛异常,SHALL NOT 连接网关

#### Scenario: 真路径经网关 MCP 协议调用

- **WHEN** USE_MOCK_MCP=false 且 MCPClient.call 执行
- **THEN** Agent 用 MCP SDK client 连接网关,经 MCP 协议调用 list_tools / call_tool,SHALL NOT 在进程内直接 HTTP 调下游

#### Scenario: 超时抛出 MCPTimeoutError

- **WHEN** 网关 MCP 响应超过 Agent 侧薄超时阈值
- **THEN** 抛出 `MCPTimeoutError`,调用方按服务故障处理(不重试,直接降级)

#### Scenario: 权限拒绝终止并审计

- **WHEN** 网关 MCP 返回权限拒绝错误
- **THEN** 终止调用并写入安全审计记录,不重试

#### Scenario: PII 明文被脱敏

- **WHEN** MCP 返回数据含明文身份证号/手机号/银行卡号
- **THEN** 返回前自动脱敏,并触发告警日志

#### Scenario: Mock 路径不回归

- **WHEN** USE_MOCK_MCP=true 运行投诉演示和单测
- **THEN** MCP 工具调用走 Mock fixtures,SHALL NOT 连接网关,现有测试全通过

### Requirement: MCP 网关双模连接

系统 SHALL 在 `app/mcp/gateway.py` 提供 `MCPGateway`,支持两种模式(由 `USE_MOCK_MCP` 决定):
- `USE_MOCK_MCP=true`(dev):进程内调用 MockMCPClient,SHALL NOT 连接网关
- `USE_MOCK_MCP=false`(prod):通过 MCP SDK client 连接独立容器网关,地址由 `MCP_GATEWAY_URL` 注入

网关 SHALL 提供连接池管理与健康检查钩子,并通过 `get_client(server: str) -> MCPClient` 工厂方法联动。`MCP_GATEWAY_URL` SHALL 在 `.env` 文件中配置。

#### Scenario: inline 模式加载 Mock Server

- **WHEN** `USE_MOCK_MCP=true`
- **THEN** `MCPGateway.get_client("business_ticket")` 返回可调用 Mock 的 `MCPClient`,SHALL NOT 发起真实网络请求,SHALL NOT 连接网关

#### Scenario: standalone 模式连接外部网关

- **WHEN** `USE_MOCK_MCP=false` 且 `MCP_GATEWAY_URL` 已配置
- **THEN** `MCPGateway` 通过 MCP SDK client 连接独立 MCP 网关,`get_client()` 返回经网关路由的 `MCPClient`

#### Scenario: 健康检查钩子可用

- **WHEN** 调用 `MCPGateway` 的健康检查方法
- **THEN** 返回各 Server 连接状态(healthy / degraded / unavailable)

## Purpose

为 complaint-service 提供 MCP 客户端统一调用封装与网关双模连接，所有外部能力（案件/工单/通话质检/RAG/数据中台）严格通过 MCP 协议访问，禁止直连业务库或内部 HTTP。

## ADDED Requirements

### Requirement: MCP Client 统一调用封装

系统 SHALL 在 `app/mcp/client.py` 提供 `MCPClient`，核心方法 `async call(server: str, tool: str, params: dict, *, trace_id: str, session_id: str) -> dict`，具备：
- 超时控制（可配置，默认对齐 common 默认值）
- 重试策略：仅业务类错误重试（最多 3 次指数退避）；服务故障不重试直接降级；权限拒绝终止并触发安全审计
- 调用审计：每次调用记录 server/tool/params 摘要/耗时/状态/错误信息，结构对齐 `complaint_agent_logs.mcp_calls`（JSONB）
- 返回前 PII 脱敏二次校验：复用 `common.security.mask`，检测到明文 PII 则脱敏并告警
- 异常类型：`MCPTimeoutError`、`MCPError`（优先复用批次0 `tests/mock/mcp/errors.py`；`app/mcp/errors.py` 可 re-export，禁止两套不兼容异常类）
- Server 名与 tool 清单对齐 PRD 4.4 / Mock `SERVERS`：`business_ticket` / `call_qc` / `rag_kb` / `data_platform`（共 14 tool）；禁止使用 `case_server` 等别名

【REQ-CJ-002】禁止直连传统业务系统数据库或内部 HTTP 接口，所有外部能力只走 MCP 网关鉴权审计。

#### Scenario: call 可对 Mock Server 返回 dict

- **WHEN** `MCPClient.call("business_ticket", "query_case_info", {"case_id": "c1"}, trace_id="t1", session_id="s1")` 在 Mock MCP Server 环境下执行
- **THEN** 返回 `dict`，含预设案件数据，不抛异常

#### Scenario: 超时抛出 MCPTimeoutError

- **WHEN** MCP Server 响应超过超时阈值
- **THEN** 抛出 `MCPTimeoutError`，调用方按服务故障处理（不重试，直接降级）

#### Scenario: 权限拒绝终止并审计

- **WHEN** MCP Server 返回权限拒绝错误
- **THEN** 终止调用并写入安全审计记录，不重试

#### Scenario: PII 明文被脱敏

- **WHEN** MCP 返回数据含明文身份证号/手机号/银行卡号
- **THEN** 返回前自动脱敏，并触发告警日志

### Requirement: MCP 网关双模连接

系统 SHALL 在 `app/mcp/gateway.py` 提供 `MCPGateway`，支持两种模式（由 `ComplaintSettings.MCP_GATEWAY_MODE` 决定）：
- `inline`（dev）：进程内调用，直接加载批次0 `tests/mock/mcp/mock_client.py` 的 Mock Server，不发起真实网络请求
- `standalone`（prod）：连接独立容器网关，地址由配置 `MCP_GATEWAY_URL` 注入

网关 SHALL 提供连接池管理与健康检查钩子，并通过 `get_client(server: str) -> MCPClient` 工厂方法与 3.1 联动。

#### Scenario: inline 模式加载 Mock Server

- **WHEN** `MCP_GATEWAY_MODE=inline`
- **THEN** `MCPGateway.get_client("business_ticket")` 返回可调用 Mock 的 `MCPClient`，不发起真实网络请求

#### Scenario: standalone 模式连接外部网关

- **WHEN** `MCP_GATEWAY_MODE=standalone` 且 `MCP_GATEWAY_URL` 已配置
- **THEN** `MCPGateway` 通过 HTTP/SSE 连接外部 MCP 网关，`get_client()` 返回经网关转发的 `MCPClient`

#### Scenario: 健康检查钩子可用

- **WHEN** 调用 `MCPGateway` 的健康检查方法
- **THEN** 返回各 Server 连接状态（healthy / degraded / unavailable）

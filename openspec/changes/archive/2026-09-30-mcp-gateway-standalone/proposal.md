## Why

当前 complaint-service 进程内嵌 4 个 MCP 桥(rag_kb / business_ticket / call_qc / data_platform),由 `RealMCPTransport` 在进程内直接 HTTP 调下游服务,`MCPGateway` 仅是连接工厂而非独立服务。此架构违反项目硬性约束:"MCP 网关在生产环境必须部署为独立容器;禁止在业务流程中嵌入式部署"。需要将 MCP 网关抽离为独立容器服务,使 Agent 调用下游工具统一走 MCP 协议经网关路由,为后续多 Agent 接入和统一治理奠定基础。

## What Changes

- 新建 `services/mcp-gateway/` 独立服务,Docker 容器化部署,使用 MCP SDK server 暴露 `list_tools` + `call_tool`
- 网关内嵌 rag_kb / business_ticket / data_platform 工具实现(从 complaint-service 进程内桥平移),内部 HTTP 调下游服务
- call_qc 工具混合实现:query_call_asr 内嵌网关(HTTP 调 data-platform qc-source 接口,从 complaint-service call_qc_bridge 平移),query_qc_violations / query_qc_alerts 由网关用 MCP client 协议调 qc-service MCP endpoint
- qc-service 的 `CallQcServer` 暴露为 MCP server endpoint,供网关用 MCP 协议调用 query_qc_violations / query_qc_alerts
- 网关承接可靠性层(per-server 熔断 / 超时 30s / 业务错重试 3 次 / per-server 固定窗口限流),从 `common/mcp/reliability.py` 迁移
- 网关出口 PII 脱敏(复用 `common.security.mask`),Agent 侧保留二次校验作为 defense-in-depth
- 网关审计层记录 `mcp_calls` 结构化日志,字段对齐 `complaint_agent_logs.mcp_calls` JSONB,trace_id 全链路串联
- 网关鉴权模块(API Key / JWT),默认关闭(`ENABLE_AUTH=false`),鉴权失败返回 B0002
- complaint-service `MCPClient` 新增 MCP SDK client transport,连接网关 MCP server;`USE_MOCK_MCP=true` 路径保持不回归
- Docker Compose 新增 mcp-gateway service,环境变量显式传递
- 保留 `USE_MOCK_MCP` 进程级开关,Mock 路径和现有测试不回归

## Capabilities

### New Capabilities

- `platform/mcp-gateway`: 独立 MCP 网关服务,对 Agent 暴露 MCP 协议(list_tools + call_tool),内嵌工具实现路由到下游,承接可靠性层/审计/PII 脱敏/鉴权/限流

### Modified Capabilities

- `complaint-service/mcp-client`: MCPClient transport 从进程内桥改为 MCP SDK client 连接网关;保留 Mock 路径和 PII 二次校验
- `complaint-service/mcp-real-path`: RealMCPTransport 从进程内桥分发改为网关 MCP 协议调用;USE_MOCK_MCP 路径不变
- `qc-service/mcp-call-qc`: CallQcServer 暴露 MCP HTTP endpoint,供网关用 MCP 协议调用;现有 HTTP API 不变
- `platform/mcp-reliability`: 可靠性层从 Agent 进程内迁移到网关;Agent 侧保留薄超时
- `platform/integration-boundaries`: MCP 网关作为独立容器,Agent 工具调用经网关路由

## Impact

- **新增代码**: `services/mcp-gateway/` 全新服务(FastAPI + MCP SDK server + 工具实现 + 可靠性层 + 审计 + 鉴权)
- **修改代码**: complaint-service `app/mcp/client.py`(新增 MCP SDK client transport) / `real_transport.py`(USE_MOCK_MCP=false 时走 MCP SDK client 而非进程内桥) / `gateway.py`(连接网关 MCP server);qc-service `app/mcp/server.py`(暴露 MCP HTTP endpoint)
- **配置**: `.env` 新增 `MCP_GATEWAY_URL` / `MCP_GATEWAY_ENABLE_AUTH` / `MCP_GATEWAY_AUTH_API_KEY`;compose 新增 mcp-gateway service
- **依赖**: 新增 `mcp` Python SDK 依赖
- **不影响**: rag-service / data-platform 代码不改;Graph 节点逻辑不改(仍调 `mcp.call`);现有测试断言不改

## Context

当前 complaint-service 进程内嵌 4 个 MCP 桥(`RealMCPTransport` + `RagKbBridge` / `BusinessTicketBridge` / `CallQcBridge` / `DataPlatformBridge`),在进程内直接 HTTP 调下游服务。`MCPGateway` 仅是连接工厂,`common/mcp/reliability.py` 在 Agent 进程内执行熔断/重试。此架构违反项目硬性约束"MCP 网关必须独立容器化"。`RealMCPTransport._forward_gateway` 已预留 HTTP 转发到 `MCP_GATEWAY_URL` 的分支但当前未走。qc-service 已有 `CallQcServer` 类(list_tools + call)但未暴露为 MCP endpoint。

## Goals / Non-Goals

**Goals:**
- 将 MCP 网关抽离为独立容器服务 `services/mcp-gateway/`
- 网关对 Agent 暴露 MCP 协议(list_tools + call_tool),Agent 用 MCP SDK client 连接
- 工具实现采用混合方案:网关内嵌 rag_kb/business_ticket/data_platform 工具(HTTP 调下游);call_qc 工具混合实现——query_call_asr 内嵌网关(HTTP 调 data-platform qc-source),query_qc_violations/query_qc_alerts 经 MCP 协议调 qc-service MCP endpoint
- 可靠性层(熔断/重试/超时/限流)从 Agent 进程迁移到网关
- 网关出口 PII 脱敏 + Agent 保留二次校验(defense-in-depth)
- USE_MOCK_MCP=true 路径不回归

**Non-Goals:**
- 不改 rag-service / data-platform 代码
- 不改 Graph 节点逻辑(仍调 `mcp.call`)
- 不做 LLM 网关
- 不做工具 schema 动态发现(本期静态注册)
- 不做 call-service / case-service / review-service
- 不做多租户隔离

## Decisions

### D1: 工具实现位置 — 混合方案(C)

**选择**: 网关对 Agent 暴露 MCP 协议;rag_kb/business_ticket/data_platform 工具实现内嵌在网关,网关内部 HTTP 调下游;`call_qc` server 采用混合实现——`query_call_asr` 内嵌在网关(HTTP 调 data-platform qc-source 接口),`query_qc_violations`/`query_qc_alerts` 由网关用 MCP client 调用 qc-service MCP endpoint。

**理由**: rag-service 和 data-platform 当前无 MCP server 基础,改造为 MCP server 工作量大且收益有限(它们只暴露 HTTP API)。qc-service 已有 `CallQcServer` 类,直接暴露为 MCP endpoint 成本低。`query_call_asr` 的数据来源是 data-platform 的 qc-source 接口(现有 complaint-service `call_qc_bridge._query_call_asr` 实现),qc-service 明确不实现该工具(归 call-service,本期不做),因此将其内嵌到网关复用现有 HTTP 调 data-platform 的逻辑。混合方案让网关成为 Agent 的统一 MCP 入口(标准化),内部按服务现状分流(务实)。

**备选**:
- 方案 A(全部内嵌网关):简单但 qc-service 已有 MCP server 逻辑,重复实现浪费
- 方案 B(每个服务都暴露 MCP server):标准化最好但 rag-service/data-platform 改造量大

### D2: 协议 — MCP 协议(SDK client)

**选择**: Agent 用 MCP SDK client 连接网关 MCP server;网关用 MCP SDK server 暴露 list_tools + call_tool;transport 用 HTTP/SSE。

**理由**: 用户要求 MCP 协议,且前瞻性架构。MCP SDK(mcp Python 包)已相对成熟,支持 HTTP/SSE transport。Agent 侧 `MCPClient` 改造为 MCP SDK client wrapper,保留 `call(server, tool, params)` 接口不变,Graph 节点无感知。

**备选**: HTTP 网关(Agent 用 httpx POST /mcp/call)— 改动最小但非标准 MCP 协议,不符合用户要求。

### D3: 可靠性层 — 网关承接 + Agent 保留薄超时

**选择**: `common/mcp/reliability.py` 的熔断/重试/超时逻辑迁移到网关。Agent 侧保留网关 HTTP 调用薄超时(快速失败),SHALL NOT 在进程内执行熔断/重试。

**理由**: 网关作为多 Agent 共享的可靠性治理层,熔断状态可跨 Agent 共享。Agent 侧薄超时确保网关挂了时 Agent 能快速降级。重试单层在网关实现,避免 Agent 与网关双重重试。

**备选**: 双层可靠性(Agent + 网关各自熔断)— 逻辑重复,熔断状态不一致。

### D4: PII 脱敏 — 网关出口 + Agent 二次校验

**选择**: 网关出口用 `common.security.mask` 递归脱敏;Agent 侧 `MCPClient._sanitize_result` 保留作为 defense-in-depth。

**理由**: 硬性约束要求"PII 敏感字段返回给智能体前必须脱敏",网关出口是数据离开网关的最后一关,在此脱敏确保所有 Agent 受益。Agent 二次校验作为纵深防御,防止网关被绕过。

### D5: qc-service MCP endpoint 暴露方式

**选择**: 在 qc-service 现有 FastAPI 应用上加 MCP HTTP endpoint(如 `/mcp` SSE),复用 `CallQcServer` 的 list_tools 和 call 逻辑。

**理由**: `CallQcServer` 已有完整的 list_tools + call 逻辑,只需加一个 MCP transport 层暴露。不改现有 HTTP API,不影响现有测试。

### D6: USE_MOCK_MCP 开关语义不变

**选择**: `USE_MOCK_MCP=true` 走 MockMCPClient 不连网关;`USE_MOCK_MCP=false` 走网关 MCP 协议。进程级开关,默认 true。

**理由**: 保持现有投诉演示和单测不回归。闭环验收时切 `USE_MOCK_MCP=false`。

## Risks / Trade-offs

- **[网关单点故障]** → 网关挂了所有 Agent 工具调用失败。缓解:Agent 侧薄超时快速降级;网关容器支持重启;未来可多实例部署。
- **[MCP SDK 成熟度]** → MCP 协议较新,SDK 可能有边界 case。缓解:先用 HTTP/SSE transport(最稳定);保留 USE_MOCK_MCP 路径作为 fallback。
- **[平移桥代码引入 bug]** → 4 个桥从 complaint-service 平移到网关可能丢失上下文。缓解:逐个平移 + 闭环验收(run_min_loop.py 11/11)。
- **[qc-service MCP endpoint 兼容性]** → 暴露 MCP endpoint 可能影响现有 FastAPI 路由。缓解:MCP endpoint 用独立路径 `/mcp`,不影响现有路由。
- **[性能:多一跳]** → Agent → 网关 → 下游,多一跳网络延迟。缓解:网关与下游同 docker network,延迟可控;网关内 httpx 连接池复用。

## Migration Plan

1. **Phase 1 — 网关脚手架**(对应 tasks 1.1-1.5): 新建 `services/mcp-gateway/`,FastAPI + MCP SDK server 基座,list_tools + call_tool 框架,health + metrics 端点
2. **Phase 2 — 工具实现迁移**(对应 tasks 2.1-2.6): rag_kb/business_ticket/data_platform 三个桥从 complaint-service 平移到网关;call_qc 的 query_call_asr 也平移到网关(HTTP 调 data-platform qc-source);工具 schema 静态注册;call_tool 按 server 名路由
3. **Phase 3 — qc-service MCP 暴露**(对应 tasks 3.1-3.3): CallQcServer 加 MCP HTTP endpoint,网关 MCP client 连接 qc-service,调用 query_qc_violations/query_qc_alerts
4. **Phase 4 — 可靠性层迁移**(对应 tasks 4.1-4.5): common/mcp/reliability.py 逻辑迁入网关,per-server 熔断/限流/超时/重试
5. **Phase 5 — 审计/PII/鉴权**(对应 tasks 5.1-6.2): mcp_calls 审计日志、出口 PII 递归脱敏、网关鉴权模块(默认关闭)
6. **Phase 6 — Agent 侧改造**(对应 tasks 7.1-7.5): complaint-service MCPClient 新增 MCP SDK client transport,USE_MOCK_MCP 路径保留
7. **Phase 7 — Compose + 验收**(对应 tasks 8.1-9.9): compose 加 mcp-gateway service,run_min_loop.py 11/11 验收,连续两次执行幂等验证

**回滚策略**: `USE_MOCK_MCP=true` 立即回滚到 Mock 路径;complaint-service 保留进程内桥代码直到验收通过后再清理(本期不清理,仅迁移到网关)。

## Open Questions

无。所有决策已在前序探索中与用户确认。

## Purpose

定义独立 MCP 网关服务作为 Agent 调用下游工具的统一入口,使用 MCP 协议暴露 list_tools + call_tool,内嵌工具实现路由到下游服务,承接可靠性层(熔断/重试/超时/限流)、审计、PII 脱敏和鉴权,使 Agent 与下游服务解耦。

## ADDED Requirements

### Requirement: 网关独立容器化部署

mcp-gateway SHALL 作为独立 Docker 容器部署,SHALL NOT 嵌入任何业务服务进程。Docker Compose SHALL 显式声明 mcp-gateway service,SHALL 通过 docker network 服务名访问下游服务,SHALL NOT 使用 `host.docker.internal` 等宿主机端口映射。网关 SHALL 暴露健康检查端点 `/health` 和 Prometheus metrics 端点 `/metrics`。

#### Scenario: 网关独立容器启动

- **WHEN** 执行 docker-compose up 启动 mcp-gateway
- **THEN** mcp-gateway 作为独立容器运行,不嵌入 complaint-service / qc-service / rag-service / data-platform 进程

#### Scenario: 网关通过服务名访问下游

- **WHEN** 网关调用 rag-service / data-platform / qc-service
- **THEN** 使用 docker network 服务名(如 `rag-service:8000`，容器内端口),SHALL NOT 使用 `host.docker.internal` 端口映射

#### Scenario: 健康检查通过

- **WHEN** GET /health
- **THEN** 返回统一信封 `{"code":"0","message":"ok","data":{"status":"healthy"},"trace_id":"..."}`

### Requirement: MCP 协议暴露

网关 SHALL 使用 MCP SDK server 暴露 `list_tools` 和 `call_tool`。transport SHALL 支持 HTTP/SSE。`list_tools` SHALL 聚合所有后端工具 schema 返回。`call_tool` SHALL 按 server 名路由到对应后端。Agent SHALL 使用 MCP SDK client 连接网关。trace_id / session_id SHALL 经 MCP 协议 metadata 透传。

#### Scenario: list_tools 返回全部工具

- **WHEN** Agent 调用 list_tools
- **THEN** 返回 rag_kb / business_ticket / call_qc / data_platform 全部已注册工具的 schema,其中 call_qc 含 query_call_asr / query_qc_violations / query_qc_alerts 三个工具

#### Scenario: call_tool 路由到对应后端

- **WHEN** Agent 调用 call_tool 指定 server="rag_kb" tool="search_regulations"
- **THEN** 网关路由到 rag_kb 工具实现,HTTP 调 rag-service,返回结果

#### Scenario: 未知 server 返回错误

- **WHEN** Agent 调用 call_tool 指定未知 server 名
- **THEN** 网关返回明确错误,SHALL NOT 转发

### Requirement: 工具路由(混合方案)

网关 SHALL 内嵌 rag_kb / business_ticket / data_platform 工具实现,HTTP 调下游服务。`call_qc` server SHALL 采用混合实现:`query_call_asr` 内嵌在网关(HTTP 调 data-platform qc-source 接口),`query_qc_violations` / `query_qc_alerts` 由网关用 MCP client 连接 qc-service MCP server endpoint 调用。工具 schema SHALL 静态注册。rag_kb 工具 SHALL 调 rag-service `POST /api/v1/kb/search`。business_ticket 工具 SHALL 调 data-platform 快照接口。data_platform 工具 SHALL 调 data-platform 快照 PUT 接口。

#### Scenario: rag_kb 工具经网关调 rag-service

- **WHEN** 网关收到 rag_kb.search_regulations 调用
- **THEN** 网关内部 HTTP 调 rag-service POST /api/v1/kb/search,返回结果

#### Scenario: call_qc.query_call_asr 内嵌网关调 data-platform

- **WHEN** 网关收到 call_qc.query_call_asr 调用
- **THEN** 网关内部 HTTP 调 data-platform qc-source 接口,返回 ASR fixture 列表

#### Scenario: call_qc.query_qc_violations 经 MCP 协议调 qc-service

- **WHEN** 网关收到 call_qc.query_qc_violations 调用
- **THEN** 网关用 MCP client 连接 qc-service MCP server endpoint,返回违规明细

#### Scenario: business_ticket 工具经网关调 data-platform

- **WHEN** 网关收到 business_ticket.query_case_info 调用
- **THEN** 网关内部 HTTP 调 data-platform 快照接口,返回结果

### Requirement: 网关可靠性层

网关 SHALL 承接 MCP 调用可靠性编排:per-server 熔断(key=`csmp:mcp_degraded:{server}`,fail_max=5,reset_timeout=30s)、超时 total=30s、业务错重试 3 次(超时/权限拒绝不重试)。重试 SHALL 单层实现,SHALL NOT 与下游重试叠加。桥下游 5xx/超时 SHALL 返回空列表或默认值,SHALL NOT 抛异常转人工审核。超时层级:MCP total=30s < LLM total=120s。

#### Scenario: per-server 熔断 open

- **WHEN** rag_kb server 累计 5 次连续失败
- **THEN** key `csmp:mcp_degraded:rag_kb` 熔断器 open,后续对 rag_kb 的调用被拒绝,30s 后放 half-open 探针

#### Scenario: 一个 server 熔断不影响另一个

- **WHEN** rag_kb 熔断器 open
- **THEN** 对 call_qc server 的调用不受影响

#### Scenario: 下游 5xx 返回空列表

- **WHEN** data-platform 返回 5xx
- **THEN** 网关返回空列表或默认值,SHALL NOT 抛异常

#### Scenario: 超时不重试

- **WHEN** rag-service 响应超过 30s
- **THEN** 网关不重试,标记该 server 降级,返回超时错误

### Requirement: 网关 per-server 限流

网关 SHALL 实现 per-server 固定窗口限流。限流命中 SHALL 返回 429 + `csmp.rate_limit.hit` 事件。Redis 故障时 SHALL fail-open 放行,SHALL NOT 阻断 MCP 调用。

#### Scenario: 限流命中返回 429

- **WHEN** 某 server 限流窗口配额耗尽
- **THEN** 网关返回 429 状态码,发出 csmp.rate_limit.hit 事件

#### Scenario: Redis 故障 fail-open

- **WHEN** Redis 不可用
- **THEN** 网关放行调用,SHALL NOT 阻断 MCP 调用

### Requirement: 网关审计

网关 SHALL 记录每次 MCP 调用审计:server / tool / params 摘要 / trace_id / session_id / latency_ms / started_at / ended_at / status / error。日志结构 SHALL 对齐 `complaint_agent_logs.mcp_calls` JSONB。SHALL 经 `call_log_sink` emit。trace_id SHALL 全链路串联。

#### Scenario: 调用成功记录审计

- **WHEN** MCP 调用成功
- **THEN** 审计日志记录 status=success,含 latency_ms 和 trace_id

#### Scenario: 调用失败记录审计

- **WHEN** MCP 调用失败(超时/熔断/业务错)
- **THEN** 审计日志记录 status=error 和 error 字段,含 trace_id

### Requirement: 网关 PII 出口脱敏

网关 SHALL 在返回前对结果递归执行 `common.security.mask` 脱敏,覆盖姓名 / 手机号 / 身份证 / 银行卡号。检测到明文 PII SHALL 触发 `mcp_pii_leak_sanitized` 审计事件。脱敏后的数据 SHALL NOT 含明文 PII。

#### Scenario: 返回值含明文 PII 被脱敏

- **WHEN** 下游返回含明文手机号的数据
- **THEN** 网关出口数据手机号已脱敏,触发 mcp_pii_leak_sanitized 审计事件

#### Scenario: 嵌套结构递归脱敏

- **WHEN** 下游返回嵌套 dict/list 含明文身份证
- **THEN** 网关递归脱敏,所有层级身份证已脱敏

### Requirement: 网关鉴权(默认关闭)

网关 SHALL 支持 API Key 恒定时间比对 / JWT 验签鉴权。`ENABLE_AUTH=false` 时 SHALL 直通。鉴权失败 SHALL 返回 B0002(未认证),SHALL NOT 误用 B0007(系统内部错误)。鉴权作用于 MCP 入站调用(tools/list / tools/call,即 `POST /mcp`);`ENABLE_AUTH=true` 时 `GET /metrics` SHALL 同样要求有效凭证(Prometheus 抓取端经 Authorization Bearer 配置);`GET /health` SHALL 始终放行(容器编排健康探针不带业务凭证)。

#### Scenario: 鉴权关闭直通

- **WHEN** ENABLE_AUTH=false
- **THEN** 网关不校验凭证,所有请求直通

#### Scenario: 鉴权失败返回 B0002

- **WHEN** ENABLE_AUTH=true 且请求未携带有效凭证
- **THEN** 网关返回 B0002(未认证),SHALL NOT 返回 B0007;对 `POST /mcp` 的拒绝使用 JSON-RPC error 信封(code=-32001,error.data.code=B0002)并回显请求 id,遵循 MCP 协议端点不包装统一信封的约定

#### Scenario: 鉴权开启时健康检查仍放行

- **WHEN** ENABLE_AUTH=true 且 GET /health 不带凭证
- **THEN** 返回 200 健康检查信封,SHALL NOT 被鉴权拦截

#### Scenario: 鉴权开启时指标端点受保护

- **WHEN** ENABLE_AUTH=true 且 GET /metrics 无有效凭证
- **THEN** 返回 401/B0002;携带有效凭证时返回 Prometheus exposition 文本

### Requirement: 网关信封一致性

网关 HTTP 管理端点 `/health` SHALL 使用统一信封 `{"code","message","data","trace_id"}`。`/metrics` SHALL 返回 Prometheus 原生 exposition 格式(`text/plain; version=0.0.4`),SHALL NOT 包装统一信封——JSON 信封无法被 Prometheus server 解析,会导致指标抓取失败。MCP 协议端点(`POST /mcp`)遵循 MCP 规范,SHALL NOT 包装统一信封;协议层前的拒绝(429/401)SHALL 使用 JSON-RPC error 信封并在 error.data 携带业务原因码。

#### Scenario: 健康检查端点返回统一信封

- **WHEN** GET /health
- **THEN** 返回 `{"code":"0","message":"ok","data":{...},"trace_id":"..."}`

#### Scenario: 指标端点返回 Prometheus 原生格式

- **WHEN** GET /metrics(携带有效凭证或 ENABLE_AUTH=false)
- **THEN** 返回 Content-Type 为 text/plain; version=0.0.4 的 Prometheus exposition 文本,SHALL NOT 出现统一信封包装

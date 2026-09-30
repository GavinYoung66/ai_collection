## 1. 网关脚手架

- [x] 1.1 新建 `services/mcp-gateway/` 项目结构(app/main.py, app/config.py, requirements.txt, Dockerfile),验证目录结构完整
- [x] 1.2 创建 FastAPI 应用基座,实现 `/health` 端点返回统一信封 `{"code":"0","message":"ok","data":{"status":"healthy"},"trace_id":"..."}`,验证 curl /health 返回 200
- [x] 1.3 添加 `mcp` Python SDK 依赖到 requirements.txt,验证 pip install 成功
- [x] 1.4 实现 MCP SDK server 基座(list_tools + call_tool 框架),验证 MCP server 可启动并响应 list_tools 空列表
- [x] 1.5 实现 Prometheus `/metrics` 端点,验证 curl /metrics 返回指标数据

## 2. 工具实现迁移(内嵌网关)

- [x] 2.1 将 `services/complaint-service/app/mcp/rag_kb_bridge.py` 平移到网关,实现 rag_kb 工具(search_regulations, search_response_templates)HTTP 调 rag-service POST /api/v1/kb/search,验证 list_tools 返回 rag_kb 的 search_regulations 和 search_response_templates 工具 schema
- [x] 2.2 将 `services/complaint-service/app/mcp/business_ticket_bridge.py` 平移到网关,实现 business_ticket 工具(query_case_info, query_history_tickets, query_history_complaints)HTTP 调 data-platform 快照接口,验证 list_tools 返回 business_ticket 的 query_case_info/query_history_tickets/query_history_complaints 工具 schema
- [x] 2.3 将 `services/complaint-service/app/mcp/data_platform_bridge.py` 平移到网关,实现 data_platform 工具(write_session, write_human_review_truth)HTTP 调 data-platform 快照 PUT 接口,验证 list_tools 返回 data_platform 的 write_session 和 write_human_review_truth 工具 schema
- [x] 2.4 将 `services/complaint-service/app/mcp/bridge_http.py`(BridgeHttpClient + 信封解包)平移到网关作为共享 HTTP 基座,验证三个工具实现复用该基座
- [x] 2.5 实现工具路由层:call_tool 按 server 名分发到对应工具实现,未知 server 返回明确错误,验证 call_tool 路由正确
- [x] 2.6 将 `services/complaint-service/app/mcp/call_qc_bridge.py` 的 `query_call_asr` 实现(含 `map_qc_source_to_asr_fixture`、`normalize_speaker`)平移到网关,HTTP 调 data-platform `GET /api/v1/data/sessions/{session_id}/qc-source` 接口,验证 list_tools 返回 call_qc 含 query_call_asr 且 call_tool 调用返回 ASR fixture 列表

## 3. qc-service MCP server 暴露

- [x] 3.1 在 qc-service 现有 FastAPI 应用上加 MCP HTTP endpoint(如 `/mcp` SSE),复用 `CallQcServer` 的 list_tools 和 call 逻辑,验证 MCP endpoint 可响应 list_tools 返回 query_qc_violations 和 query_qc_alerts schema
- [x] 3.2 验证 qc-service 现有 HTTP API(如 GET /api/v1/qc/records/{session_id})行为不变,现有测试全通过
- [x] 3.3 在网关实现 MCP client 连接 qc-service MCP endpoint,验证网关经 MCP 协议调用 call_qc.query_qc_violations 返回违规明细

## 4. 可靠性层迁移

- [x] 4.1 将 `common/mcp/reliability.py` 的 per-server 熔断(key=csmp:mcp_degraded:{server}, fail_max=5, reset_timeout=30s)迁移到网关,验证连续 5 次失败后熔断 open 拒绝调用
- [x] 4.2 将超时(30s)和业务错重试(3 次,超时/权限拒绝不重试)迁移到网关,验证超时不重试、业务错重试 3 次
- [x] 4.3 实现 per-server 固定窗口限流,验证限流命中返回 429 + csmp.rate_limit.hit 事件,Redis 故障时 fail-open 放行
- [x] 4.4 验证重试单层实现(网关重试不与下游重试叠加);分别验证 rag_kb(rag-service)、business_ticket(data-platform)、data_platform(data-platform)、call_qc.query_call_asr(data-platform)、call_qc.query_qc_violations(qc-service)的下游 5xx/超时均返回空列表或默认值不抛异常
- [x] 4.5 验证 MCP total=30s < LLM total=120s 超时层级关系

## 5. 审计与 PII 脱敏

- [x] 5.1 在网关实现 mcp_calls 审计日志(server/tool/params 摘要/trace_id/session_id/latency_ms/started_at/ended_at/status/error),验证成功和失败调用均记录审计
- [x] 5.2 实现审计日志经 call_log_sink emit,验证日志结构对齐 complaint_agent_logs.mcp_calls JSONB
- [x] 5.3 在网关出口实现 PII 递归脱敏(common.security.mask),验证返回值含明文 PII(姓名/手机号/身份证/银行卡号)时被脱敏
- [x] 5.4 验证检测到明文 PII 触发 mcp_pii_leak_sanitized 审计事件

## 6. 网关鉴权

- [x] 6.1 实现网关鉴权模块(API Key 恒定时间比对 / JWT 验签),ENABLE_AUTH=false 时直通,验证关闭时不校验凭证
- [x] 6.2 验证鉴权失败返回 B0002(未认证,SHALL NOT 返回 B0007)

## 7. Agent 侧改造

- [x] 7.1 在 complaint-service MCPClient 新增 MCP SDK client transport,连接网关 MCP server,验证 USE_MOCK_MCP=false 时 Agent 经 MCP 协议调用网关
- [x] 7.2 修改 RealMCPTransport,USE_MOCK_MCP=false 时走 MCP SDK client 而非进程内桥,验证不调进程内桥代码
- [x] 7.3 保留 Agent 侧 PII 二次校验(_sanitize_result)和薄超时,验证 defense-in-depth 仍生效
- [x] 7.4 验证 USE_MOCK_MCP=true 时走 MockMCPClient 不连网关,现有投诉演示和单测全通过
- [x] 7.5 验证 Graph 节点仍调 mcp.call,接口不变,节点逻辑无感知

## 8. Docker Compose 集成

> 验收结果：8.1~8.4 全部 PASS。落点为 docker-compose.dev.yml（用户确认的决策：真实运行栈在此文件，base docker-compose.yml 仅 infra）。镜像构建修复两处：排除 requirements.lock 中 rocketmq-python-client（与 langfuse otlp 冲突）、runtime 补 fastapi>=0.115（兼容 mcp 带入的 starlette 1.x）。

- [x] 8.1 在 docker-compose.yml 新增 mcp-gateway service,配置 docker network 服务名访问下游,验证网关容器独立启动
- [x] 8.2 在 .env 新增 MCP_GATEWAY_URL / MCP_GATEWAY_ENABLE_AUTH / MCP_GATEWAY_AUTH_API_KEY 环境变量,验证 compose 显式传递
- [x] 8.3 配置 mcp-gateway depends_on rag-service/qc-service/data-platform(真路径模式),验证启动顺序正确
- [x] 8.4 验证网关 upstream 使用 docker network 服务名(如 rag-service:8002),SHALL NOT 使用 host.docker.internal

## 9. 闭环验收

> 验收结果：9.1~9.9 全部 PASS（run_min_loop.py 13/13；熔断真路径 + 单测双验证）。详见下方「10. 验收期修复记录」。

- [x] 9.1 USE_MOCK_MCP=false 时启动全部服务(complaint-service + mcp-gateway + rag-service + qc-service + data-platform),验证所有服务健康检查通过
- [x] 9.2 执行 run_min_loop.py,验证 11/11 项全部 PASS(judge 成功经网关组装证据包、停在 human_review、返回 thread_id)
- [x] 9.3 验证 review approve 触发 write_session 经网关回写快照投诉列,快照行 complaint_id/verdict/task_status 非空
- [x] 9.4 连续两次执行 run_min_loop.py,验证无重复数据(幂等性)
- [x] 9.5 验证网关 mcp_calls 审计日志含 trace_id 全链路串联
- [x] 9.6 验证网关出口 PII 已脱敏(姓名/手机号/身份证/银行卡号无明文)
- [x] 9.7 验证 per-server 熔断 open 时返回 MCPCircuitOpenError 不触达下游
- [x] 9.8 验证 USE_MOCK_MCP=true 时现有测试全通过,不连网关
- [x] 9.9 输出验收对照表(逐项 pass/fail + 证据)和阻断问题清单,完成后不执行 git commit,等待人工审查

## 10. 验收期修复记录（Batch 6 闭环验收发现并修复的缺陷）

> 均为验收过程中暴露、不修则验收无法通过的真实缺陷，非绕过。按「暴露顺序」记录。

### 10.1 MCP server DNS-rebinding 防护误拒 docker 服务名（421 元凶）
- **文件**：`services/mcp-gateway/app/mcp_server.py`
- **现象**：complaint-service 经 `streamable_http_client` 调 `http://mcp-gateway:9000/mcp` 被拒 `421 Misdirected Request`，网关日志 `WARNING Invalid Host header (transport_security.py:71)`。
- **根因**：mcp SDK 1.30 `FastMCP` 默认 `enable_dns_rebinding_protection=True` 且 `allowed_hosts=[]`，`Host: mcp-gateway:9000` 不在白名单。
- **修复**：构造 `GatewayFastMCP` 时注入 `TransportSecuritySettings(allowed_hosts=["mcp-gateway:*", "localhost", "127.0.0.1", "::1"])`，保留防护、只加白名单。

### 10.2 sample_backflow 回写缺 case_id/complaint_id（快照投诉列永远空）
- **文件**：`services/complaint-service/app/graph/nodes/sample_backflow.py`
- **现象**：write_session 返回 `backflow_resolve_miss`，快照 `dw.ads_case_loop` 投诉三列恒空。
- **根因**：节点只传 `session_id/task_status/verdict`；网关跨进程无法反查 session→case 映射（默认 resolver 返回 None），design D1「① params 直带 complaint_id/case_id」未落实。
- **修复**：payload 补 `case_id=state.case_ids[0]`、`complaint_id=state.complaint_id`。

### 10.3 图节点 MCP 调用未透传 trace_id/session_id（全链路串联断裂）
- **文件**：`services/complaint-service/app/graph/nodes/{evidence_case,evidence_call_qc,compliance,generate_package,sample_backflow}.py`
- **现象**：网关 mcp_calls 审计的 trace_id 是 HTTP 层新 UUID，非业务 trace_id，无法全链路追溯。
- **根因**：`mcp.call(...)` 的 `trace_id/session_id` 默认空串，图节点调用时未传（design D2 未落实）。
- **修复**：六处 `mcp.call` 统一补 `trace_id=state.trace_id, session_id=state.session_id`。

### 10.4 测试桩签名未同步真实 MCPClient.call（trace_id 修复连带）
- **文件**：`services/complaint-service/tests/unit/test_node_degrade.py`、`tests/unit/test_result_ready_publish.py`
- **现象**：10.3 修复后单测报 `_StubMCP.call() got an unexpected keyword argument 'trace_id'`。
- **修复**：测试双 `_StubMCP.call` 与三个 `_call` 补 `trace_id/session_id`（或 `**kwargs`），仅同步签名、未改断言。

### 10.5 mcp-gateway 漏配 REDIS_HOST=redis（限流/降级标志 fail-open）
- **文件**：`docker-compose.dev.yml`
- **现象**：网关日志 `rate_limit_degraded_allow ... Error connecting to localhost:6379`，限流/降级标志失效。
- **根因**：mcp-gateway service 未像其它服务那样覆盖 `REDIS_HOST`，从根 .env 拿到宿主机视角的 `localhost`。
- **修复**：environment 补 `REDIS_HOST: redis` + `REDIS_PORT: 6379`。

### 10.6 RocketMQ 消费不可见时长 30s < handler 37s（消费端卡死）
- **文件**：`common/eventbus/rocketmq.py`
- **现象**：complaint.created handler（真 LLM 研判 ~37s）ACK 报 `40013 INVALID_RECEIPT_HANDLE`，消息反复重投、消费组卡死不再消费新事件。
- **根因**：`consumer.receive(8, 30)` 的不可见时长 30s < handler 处理时长。
- **修复**：提取常量 `RECEIVE_INVISIBLE_DURATION = 300`（覆盖研判 + 慢 LLM + 重试，远小于投诉全局 30min）。

### 遗留（未修，仅记录，不阻塞本验收）
- RocketMQ DLQ topic `%DLQ%gid_cs_complaint_created` 未创建（重试耗尽时 DLQ 路由失败）。
- `verdict=pending`：真 LLM 对种子数据给不出强结论（confidence 0.3），属数据/提示词质量，非链路缺陷。
- checkpointer 仍 MemorySaver（dev 默认），生产需 `COMPLAINT_CHECKPOINTER_PG=true` + 跑 0015 迁移。

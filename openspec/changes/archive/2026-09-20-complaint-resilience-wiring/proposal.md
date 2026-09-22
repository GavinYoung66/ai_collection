## Why

P0 公共韧性零件（`common/resilience/*` + `common/llm/reliability.py` + `common/mcp/reliability.py`）已落地，但 complaint-service 仍未接线：LLM 只打 primary 无 fallback、节点 LLM 超时/熔断直接冒 500、`GraphInterrupt`（正常人审挂起）与 LLM 真错误混处理、5 维限流是私有实现、工单无 deadline、MCP 调用不走 `call_tool` 不熔断。需要把投诉研判全链路接到 P0 零件上，实现"LLM 失败不 500、排队有上限、超时转人工、MCP 熔断按 server 隔离"。

## What Changes

- **LLM 统一入口**：`ComplaintLLMProvider.invoke` 改调 `common.llm.reliability.invoke_llm(model, messages, fallback_chain=...)`，fallback_chain 从配置读取（主→备→更弱模型），不再只打 primary
- **节点降级 catch**：verdict/fact/reflection/compliance/user_demand/generate_package 6 节点 LLM 调用外包 try/except，catch `TimeoutError`/`CircuitBreakerOpenError`/`LLMCallError` 后调 `DegradeLLM` 生成规则草稿、设 `state.exception_info`、`task_status=pending_review`，不抛异常
- **GraphInterrupt 与 LLM 错误拆开**：`complaint_service._judge_with_graph` 区分 `GraphInterrupt`（正常人审挂起，不写异常）与 `TimeoutError`/`CircuitBreakerOpenError`/`RuntimeError`（LLM 真错误，转 pending_review + 写 exception_info）
- **5 维限流拼装**：`rate_limiter.py` 用 P0 零件替换私有实现（Semaphore 闸+BoundedQueue 削峰+rate_limit 令牌桶+动态消费+idempotency 单工单锁），配置从 `config/rate_limiter.yaml` 读取，阈值不变（max_active=20, queue_max=500）
- **工单 deadline**：两级独立——排队超时（BoundedQueue 等待>10min→弹出转 pending_review，A5010）+ 执行超时（graph.ainvoke 开始计时>30min→`degrade_flow.handle_global_timeout`→pending_review，A5010），用 Redis TTL 记开始时间
- **MCP 走 common.mcp.reliability**：`mcp/client.py` 的 call 改调 `common.mcp.reliability.call_tool(server, tool, payload, timeout=Timeout(total=30.0))`，连续失败 5 次熔断该 server（key=`csmp:mcp_degraded:{server}`），30s 后半开试探
- **hooks 接 publisher**：`app/main.py` 启动时 `hooks.on_event = app.events.publisher.publish`，降级/限流/熔断事件统一经本服务 EventPublisher 发出（保留各自 `csmp.{component}.{action}` 事件名；流程降级另发 `complaint.degraded`），payload 带 trace_id/code/reason/node

### 不做（Non-Goals）

- 不改 LangGraph 图结构（节点/边不变）
- 不下沉 `DegradeLLM` / `ComplaintRuleEngine` 到 common（领域策略留本服务）
- 不替换 checkpointer（仍是 MemorySaver，标 P1.5）
- 不上 Prometheus / Grafana（hooks 打计数即可）
- 不做独立 MCP 网关（P3）
- 不做 Redis Stream 队列（P5）
- 不改 QC / RAG 服务（各自 P1 单独 propose）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `complaint-service/graph-nodes`: 节点增加 LLM 超时/熔断 catch → 规则草稿 + `pending_review` 的降级行为
- `complaint-service/llm-layer`: `ComplaintLLMProvider` 改走 `invoke_llm` + `fallback_chain`，不再只打 primary
- `complaint-service/mcp-client`: MCP 调用改走 `common.mcp.reliability.call_tool` + 按 server 熔断
- `complaint-service/rate-limit`: 5 维限流改用 P0 零件拼装，阈值/用户维/Redis 挂行为不变
- `complaint-service/session-service`: `_judge_with_graph` 拆分 GraphInterrupt vs LLM 错误；新增排队 10min + 执行 30min 两级 deadline

## Impact

- **修改代码**：`app/llm/provider.py`、6 个 graph node、`complaint_service.py`、`app/service/rate_limiter.py`、`app/mcp/client.py`、`app/main.py`（启动 hooks wiring）
- **依赖**：依赖 P0 变更 `platform-resilience-primitives` 的 7 零件 + `invoke_llm` + `call_tool` 已就绪可 import
- **API 契约**：LLM 降级时 HTTP 仍 200 + `task_status=pending_review`，前端看状态字段；队列满返回 B0006（数据中台退避重试）；排队/执行超时返回 A5010（转人审）
- **配置**：新增 `config/rate_limiter.yaml`（阈值沿用 max_active=20/queue_max=500）+ fallback_chain 配置项
- **架构约束**：降级策略留投诉服务不下沉；MCP 熔断按 server 隔离（rag_kb 熔断不影响 business_ticket）；GraphInterrupt 是正常流程不写异常；hooks 事件名带 `csmp.` 前缀

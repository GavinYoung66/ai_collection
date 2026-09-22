## Approach

本变更不新增能力域，只把 `platform-resilience-primitives` 的 P0 零件接线到 complaint-service 全链路。按"入口 → 节点 → 编排 → 限流 → 超时 → MCP → 事件"七层依次接入，每层独立可测，失败不影响已通过层。

### 1. LLM 入口层（provider.py → invoke_llm）

`ComplaintLLMProvider.invoke` 当前直接调 `common.llm.provider.chat`，无 fallback。改为：
```
result = await invoke_llm(
    model=primary,
    messages=messages,
    fallback_chain=config.fallback_chain,  # [qwen-plus, qwen-turbo, deepseek-r1]
    timeout=Timeout(total=120.0),
    breaker_key=f"csmp:llm_degraded:{primary}",  # 不传也会自动派生
)
```
invoke_llm 内部已编排：选 Key → 限流 → 熔断检查 → 有界重试 → fallback 换模型 → 降级 flag。provider 只需透传 fallback_chain 和 timeout。

### 2. 节点降级层（6 个 graph node）

每个节点 LLM 调用包 try/except，统一降级模板：
```python
try:
    raw = await llm.invoke(...)
    return parse(raw)
except (TimeoutError, CircuitBreakerOpenError, LLMCallError) as e:
    draft = ComplaintRuleEngine.generate_draft(state)  # 带 [规则兜底] 前缀
    state.exception_info = {"type": _map_exc(e), "node": __name__, "trace_id": state.trace_id}
    state.task_status = "pending_review"
    hooks.on_event("csmp.degrade.triggered", {"code": "A5017", "node": __name__, "trace_id": ...})
    return state  # 不抛
```
`_map_exc`：TimeoutError→timeout，CircuitBreakerOpenError→circuit_open，其余→llm_degraded。

### 3. GraphInterrupt 与 LLM 错误拆开（complaint_service._judge_with_graph）

当前一个 except 块吃所有异常。改为三分支：
```python
try:
    result = await graph.ainvoke(state, config)
except GraphInterrupt:
    # 正常人审挂起，不写异常
    session.status = "pending_review"
    return session
except (TimeoutError, CircuitBreakerOpenError, RuntimeError) as e:
    session.exception_info = {"type": type(e).__name__}
    session.status = "pending_review"
    return session
except Exception as e:
    session.exception_info = {"type": "unknown_error", "detail": str(e)}
    session.status = "pending_review"
    return session
```

### 4. 5 维限流拼装（rate_limiter.py）

用 P0 零件替换私有实现，维度映射：
| 维 | 零件 | 配置 |
|----|------|------|
| 1 并发闸 | `Semaphore(max_inflight=20)` | max_active=20 |
| 2 队列削峰 | `BoundedQueue(max_len=500)` | queue_max=500，满返回 B0006 |
| 3 MCP 令牌桶 | `rate_limit.allow(key=f"csmp:mcp:{server}", limit=qps, window=1)` | per-server |
| 4 动态消费 | 队列深度>250 x2、>400 x3 | 在 pop 侧调速 |
| 5 单工单锁 | `idempotency.acquire(key=f"csmp:complaint:judge_lock:{cid}", ttl=1800)` | 防重复执行 |

配置从 `config/rate_limiter.yaml` 读取，阈值沿用现有值。用户维 key：X-User-Id 优先回退 IP，保持不变。

### 5. 工单 deadline（session-service）

两级独立，用 Redis TTL 记开始时间：
- **排队超时**：入队时 `SET csmp:complaint:queue_start:{cid} = now EX 600`（10min）。judge_async 入口检查：若 key 不存在（已超 10min）→ 弹出 → A5010 → pending_review
- **执行超时**：graph.ainvoke 前 `SET csmp:complaint:exec_start:{cid} = now EX 1800`（30min）。超时回调 `degrade_flow.handle_global_timeout` → A5010 → pending_review

两级不合并：排队 key 在获取 slot 时删除，执行 key 在 ainvoke 完成时删除，互不干扰。

### 6. MCP 走 call_tool（mcp/client.py）

```python
async def call(self, server, tool, payload):
    return await common.mcp.reliability.call_tool(
        server=server, tool=tool, payload=payload,
        timeout=Timeout(total=30.0),
        breaker_key=f"csmp:mcp_degraded:{server}",
    )
```
熔断后该 server 调用直接降级 → 证据缺失 → 反思循环 → 最终转人工。business_ticket server 独立 key 不受影响。

### 7. hooks 接 publisher（app/main.py 启动）

```python
# app/main.py lifespan
common.resilience.hooks.on_event = app.events.publisher.publish
```
事件名规范：`csmp.{component}.{action}`（如 `csmp.rate_limit.hit` / `csmp.circuit_breaker.open` / `csmp.degrade.triggered`），限流/熔断 Redis key 均带 `csmp:` 前缀以派生合规事件名。业务降级事件 payload 带 `trace_id`/`code`/`reason`/`node` 全字段；零件层状态事件（breaker/rate_limit）在有请求上下文时经 context 透传 `trace_id`/`node`，无上下文的纯状态翻转至少携带 `key`/`reason`。publisher 不存在时 hooks 默认打 structlog 不报错。

## Design Constraints (from project memory & spec)

- 降级策略 DegradeLLM / ComplaintRuleEngine 不下沉 common，留本服务
- 5 维是本服务用零件拼的，不搬进 common
- GraphInterrupt 是正常人审流程，不是错误，不写 exception_info
- LLM 降级后 HTTP 仍 200，task_status=pending_review，前端按状态展示
- MCP 熔断按 server 隔离，rag_kb 熔断不影响 business_ticket
- 队列满返回 B0006 给数据中台退避重试，不转人工
- 排队 10min 和执行 30min 是两个独立层级，不合并
- 节点降级后产出 [规则兜底] 草稿，不是空输出
- hooks 事件名带 csmp 前缀

## Risks & Mitigations

- **风险：节点 try/except 吞掉真 bug** → exception_info 必记 type+node+trace_id，unknown_error 分支兜底，人审能看到
- **风险：deadline 误杀长查询** → 30min 是全局上限，单节点 LLM total=120s、MCP total=30s 在更内层先触发，不会先撞全局
- **风险：fallback_chain 配置错把弱模型排前面** → 配置加顺序校验，单测覆盖
- **风险：rate_limiter 重写破坏 20/60/200 行为** → 迁移后跑行为 diff 测试（7.3 验收点）
- **风险：hooks publisher 阻塞业务** → publisher 异步 fire-and-forget，失败仅 warn 不抛
- **风险：MemorySaver checkpointer 重启丢 state** → 已标 P1.5，本变更不替换，deadline 用 Redis TTL 兜底

## Open Questions

（无）—— 错误码复用 P0 已定的 A5010（超时）/A5017（LLM 熔断）/B0006（限流），无新码号需拍板。

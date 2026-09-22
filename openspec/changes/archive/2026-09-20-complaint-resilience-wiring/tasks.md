# Tasks: complaint-resilience-wiring

## 1. LLM 入口层（provider → invoke_llm）

- [x] 1.1 修改 `services/complaint-service/app/llm/provider.py` 的 `ComplaintLLMProvider.invoke`，改为调 `common.llm.reliability.invoke_llm(model, messages, fallback_chain=..., timeout=Timeout(total=120.0), breaker_key=...)`；删除只打 primary 的旧路径
- [x] 1.2 在 `config/` 新增 fallback_chain 配置项（如 `qwen-plus → qwen-turbo → deepseek-r1`），provider 启动时读取；加顺序合法性校验
- [x] 1.3 单测：主模型不可用→自动走备用成功；整链失败→degrade flag 置位；primary 熔断 open 不阻塞 backup

## 2. 节点降级层（6 个 graph node）

- [x] 2.1 在 `app/graph/nodes/verdict.py`、`fact.py`、`reflection.py`、`compliance.py`、`user_demand.py`、`generate_package.py` 的 LLM 调用外包 try/except，catch `(TimeoutError, CircuitBreakerOpenError, LLMCallError)`
- [x] 2.2 catch 块统一调 `DegradeLLM` + `ComplaintRuleEngine.generate_draft` 产出 `[规则兜底]` 草稿；设 `state.exception_info`（type/node/trace_id）；设 `state.task_status=pending_review`；调 `hooks.on_event("csmp.degrade.triggered", ...)`
- [x] 2.3 单测：mock LLM timeout=0.1s → 节点产出 `[规则兜底]` 草稿 + pending_review，不抛异常；6 个节点各跑一遍

## 3. GraphInterrupt 与 LLM 错误拆开

- [x] 3.1 修改 `complaint_service.py` 的 `_judge_with_graph`，拆为三分支：`except GraphInterrupt`（正常人审，不写异常）/ `except (TimeoutError, CircuitBreakerOpenError, RuntimeError)`（写 exception_info + pending_review）/ `except Exception`（unknown_error + pending_review）
- [x] 3.2 单测：触发 GraphInterrupt → session.pending_review 但 exception_info 为空；触发 TimeoutError → exception_info.error_type=timeout

## 4. 5 维限流拼装

- [x] 4.1 修改 `app/service/rate_limiter.py`，用 P0 零件替换私有实现：维1 `Semaphore(20)` / 维2 `BoundedQueue(500)`（满返回 B0006）/ 维3 `rate_limit.allow(key=f"csmp:mcp:{server}")`（接入 MCPClient.call 前）/ 维4 队列深度>250 x2、>400 x3 / 维5 `idempotency.acquire(key=f"csmp:complaint:judge_lock:{cid}", ttl=1800)`；用户维 acquire_user 接 judge 路由
- [x] 4.2 配置从 `config/rate_limiter.yaml` 读取（max_active=20, queue_max=500），用户维 key：X-User-Id 优先回退 IP，Redis 挂放行——全部保持不变
- [x] 4.3 单测：50 并发 → 20 在跑 + 30 在队列；队列满 501 → B0006；同 complaint_id 重复 → 第二次 acquire 返回 False；Redis 挂 → 放行不报 B0006

## 5. 工单 deadline

- [x] 5.1 排队超时：入队时 `SET csmp:complaint:queue_start:{cid} = now EX 600`（10min TTL）；judge_async 入口检查 key 不存在则弹出转 pending_review + A5010
- [x] 5.2 执行超时：graph.ainvoke 前 `SET csmp:complaint:exec_start:{cid} = now EX 1800`（30min TTL）；超时回调 `degrade_flow.handle_global_timeout` → pending_review + A5010
- [x] 5.3 两级互不干扰：排队 key 在获取 slot 时删，执行 key 在 ainvoke 完成时删
- [x] 5.4 单测：mock 排队>10min → A5010 转人审；mock 执行>30min → A5010 转人审；两级独立计时

## 6. MCP 走 call_tool

- [x] 6.1 修改 `app/mcp/client.py` 的 call，改调 `common.mcp.reliability.call_tool(server, tool, payload, timeout=Timeout(total=30.0), breaker_key=f"csmp:mcp_degraded:{server}")`
- [x] 6.2 单测：rag_kb 连续超时 5 次 → 熔断 open；熔断期间 compliance 走规则兜底；business_ticket 调用不受影响

## 7. hooks 接 publisher

- [x] 7.1 在 `app/main.py` 启动 lifespan 中 `common.resilience.hooks.on_event = app.events.publisher.publish`；事件名规范 `csmp.{component}.{action}`；payload 带 trace_id/code/reason/node；publisher 不存在时默认 structlog 不报错
- [x] 7.2 单测：触发降级 → publisher 收到 `csmp.degrade.triggered` 事件含 trace_id/code/reason/node

## 8. 验收（全量）

- [x] 8.1 LLM 故意超时（mock timeout=0.1s）→ 节点产出 `[规则兜底]` 草稿 + pending_review，HTTP 200 不是 500
- [x] 8.2 LLM 主模型不可用 → invoke_llm 自动走 fallback_chain 备用模型成功
- [x] 8.3 灌 50 条投诉并发 → 20 在跑 + 30 在队列，页面能看到排队状态
- [x] 8.4 队列满 500 后第 501 条 → 返回 B0006，数据中台可重试
- [x] 8.5 工单排队超过 10min → 自动转 pending_review（A5010）
- [x] 8.6 图执行超过 30min → 自动转 pending_review（A5010）
- [x] 8.7 MCP rag_kb 连续超时 5 次 → rag_kb 熔断，compliance 走规则兜底；business_ticket 调用不受影响
- [x] 8.8 hooks 事件能通过 complaint.degraded publisher 发出，含 trace_id/code/reason
- [x] 8.9 现有 v5 验收测试不新增红灯（基线预存失败清单见 9.7；不破坏已有功能）

## 9. 第三轮交叉验证整改

- [x] 9.1 delta 补 MODIFIED：llm-layer「LLM 熔断状态管理」与 data-layer「Redis Key 模式」，声明旧 pybreaker `LLMBreaker` 与全局永不过期键 `csmp:llm_degraded` 废弃，迁移到 common per-model `csmp:llm_degraded:{model}`（TTL 60s）
- [x] 9.2 移除 `real_transport.py` 中生产惰性的全局 `RedisDegradeTracker` 门（fallback 链只允许 common per-model 熔断）
- [x] 9.3 删除死代码 `app/llm/breaker.py` 与 `redis_keys.py` 的 `LLM_DEGRADED_KEY`/`TTL_LLM_DEGRADED`，同步清理引用与单测
- [x] 9.4 LLM 限流拒绝（common 抛 RuntimeError）在 complaint 侧映射为明确错误类型，不再以裸 "RuntimeError" 落 exception_info
- [x] 9.5 shutdown 取消在途排队研判时 best-effort 转 pending_review，避免 session 滞留 judging
- [x] 9.6 common `rate_limit.allow` 的 INCR/EXPIRE 原子化，杜绝无 TTL 计数键（同步 common/complaint/rag 三处测试 fake 至 SET NX EX + INCR 序列）
- [x] 9.7 回归：complaint 全量 pytest（预存基线 2 failed：test_reject_rejudge_back_to_evidence / test_close）、common 全量 pytest（预存基线 9 failed：callcenter HTTP400×3、speech HTTP404×4、security×2）、`openspec validate --strict` 通过

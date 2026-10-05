# Design: 投诉研判洪峰吞吐与韧性加固

## Context

见 proposal.md 的 Why 与容量目标。关键现状约束：

- 两条入口：HTTP 入口经五维限流（20 并发闸 + 500 `BoundedQueue`）；事件入口 `subscriber.handle_complaint_created → judge_async(run_graph=True)` 跳过五维限流。
- 事件消费在 `common/eventbus/rocketmq.py` 对 `receive(8)` 的批次 `for + await` 串行；每条图约 40s。
- LLM 限流阈值在 `common/llm/reliability.py:141-142` 与 `services/complaint-service/app/llm/real_transport.py:29-30` 两处重复硬编码（60/60s）。
- MCP 传输在 `app/mcp/real_transport.py:143-155` 每次调用新建 `httpx.AsyncClient` + MCP session + initialize。
- 已实测（`USE_MOCK_LLM=false`）：主模型配置 `deepseek-r1`→API 名 `deepseek-reasoner`，DeepSeek 服务端实际返回 **`deepseek-flash`**（账号并发上限 2500，按并发而非 RPM 限流）；fallback `qwen-plus`（DashScope，200 可用，配额 600+ RPM）。LLM 余量充足，瓶颈是自设 60 RPM 与串行消费。
- 现有韧性：at-least-once + DLQ（重试 3 次/共 4 次）、event_id 去重、维5幂等锁、跨进程 `graph_lock`（Lua CAS 释放，TTL=1800s）、进程内在途集合、PG checkpointer（pool_min=5）、全局执行超时 1800s、`RECEIVE_INVISIBLE_DURATION=300s`。

## Goals / Non-Goals

**Goals:**
- 稳定支撑 100 条投诉事件并发执行 + RocketMQ 磁盘队列滚动补位，不丢、不重。
- 降低单条证据采集与 MCP 调用延迟。
- 补齐 HTTP 队列满直接 429 的丢单缺陷（开关灰度）。
- 全部改动可通过环境变量/开关回滚到现状。

**Non-Goals:**
- 不按 1000 条做容量设计；不追求 worker 并发开到 20+。
- 不改主模型名（黑盒路由仅记录，后续单独处理）。
- 不改 qc/rag/data-platform 业务代码，不改业务语义（AI 仅出草稿、降级必人审）。
- 不引入新中间件；不做 HTTP 入口整体事件化改造。

## Decisions

### D1：限流阈值环境变量化 + 单一事实源
- 在公共层定义由 `LLM_RATE_LIMIT`/`LLM_RATE_WINDOW` 读取的单一配置（默认 60/60），投诉 transport 引用同一事实源，删除重复常量。
- 备选：仅在投诉侧改常量。否决——两处口径漂移正是现状问题，且公共层是其他服务共享入口。
- 故障方向不变：Redis 不可用静默放行。

### D2：证据节点单 case 内并发，多 case 暂不放大
- 仅把单 case 内相互独立的查询 `asyncio.gather`（case 内 3 个 / call_qc 内 2 个）。
- 外层多 case 是否并发：默认不扩大，受 per-server QPS（50/30/20）与下游容量约束；如压测证明有余量，作为可选优化但不改变状态装配结果。
- 异常语义：用 `gather` 时逐个捕获/或 `return_exceptions=True`，任一 MCP 失败仍写 `exception_info` 走既有降级，禁止部分失败被当成功。

### D3：MCP 复用 HTTP 连接池，不复用 MCP 有状态会话
- 实例级长生命周期 `httpx.AsyncClient`（keepalive + max_connections/max_keepalive 上限）；每次调用仍新建轻量 MCP `ClientSession` 走 stateless_http；关停 `aclose`。
- 依据：`real_transport.py:144` 注释明确"stateless_http，无需会话复用"，指 MCP 会话层；底层 TCP 连接池复用与之不冲突。
- **实现前强制动作（spike）**：核对项目锁定的 mcp SDK 版本官方文档，确认 `streamable_http_client` 复用外部 `httpx.AsyncClient` 时的连接生命周期与 stateless 行为，禁止凭记忆写 API；在单测/冒烟中验证连接数稳定与错误映射不变。

### D4：队列满溢出 Redis List（非 RocketMQ、非有界等待）
- 选 Redis List（LPUSH/BRPOP，FIFO）+ 兜底容量 + 202 + 后台补偿循环，挂 main lifespan。
- 备选 A：有界等待（满了让 HTTP 请求阻塞等槽）。否决——占用连接、高并发连接堆积。
- 备选 B：HTTP 入口也发 RocketMQ 事件。否决——把同步接口改异步、调用方需轮询，改动面大，超出补洞范围。
- 备选 C：仅返回 `Retry-After`。否决——仍依赖调用方，可能丢单。
- **Redis 故障取舍**：fail-closed 回退现有 `B0006`（溢出本是额外补偿，Redis 挂时退回确定的拒绝语义），而非放行；已在执行/排队的工单不受影响。理由：放行会让内存队列在无溢出缓冲时被打穿。
- 补偿循环：BRPOP 取出重新 `acquire_complaint`；granted 执行、queued 随内存队列、仍 full 带指数退避塞回；关停时在途/积压重新入队或依赖图锁+重投恢复，保证不丢。
- 幂等：full 时现有代码已回滚维5锁（`rate_limiter.py:179`），溢出后重新接入不会被误判 duplicate；最终一次性由维5锁+图锁+在途集合保证。

### D5：事件消费有界并行，并行度配置化，默认 =1 串行
- `rocketmq.py` 批次用 `asyncio.gather` + 模块级信号量（并行度由配置注入），=1 完全等同现状。
- 这是 `common/` 公共组件：并行度做成适配器配置项（默认 1），由各服务自行设定，避免擅自改变 qc/data-platform/rag 的消费行为；回归这些订阅链路。
- 确认粒度：handler 内部已对 complaint 做幂等/图锁；单条失败不 ACK，靠 Broker 重投 + DLQ，不影响同批。

### D6：invisible duration 与消息保留期
- 现状 `RECEIVE_INVISIBLE_DURATION=300s`，串行单条 ~40s 不触发；并行后单条仍 ~40s，正常不触发重投。保留 300s 为默认，但并行度压测时必须观测"消息在途期间是否过早回到可见"。
- 若压测出现慢单（接近/超过 300s）导致二次拉取：优先评估 RocketMQ 5.x 的 `change_invisible_time` 心跳续期；次选把不可见时长上调到与全局超时 1800s 对齐。二者均须保证不会因不可见过长而阻碍 DLQ/重平衡。
- 消息保留期：100 条滚动在分钟级完成，远离默认保留期（48h+）；spec 要求"目标消化速率下队尾不过期"作为压测断言而非调参。

### D7：并行度推算（利特尔法则 L = λW）
- 在途并发 L = 到达速率 λ × 单请求耗时 W。
- 图侧峰值 LLM 在途 = 图并行度 × 3（fact/compliance/user_demand 扇出同时刻）。并行度 16 → 48；DeepSeek flash 账号并发 2500，余量 >98%，LLM 非约束。
- 真实 W 由压测测定（真实 prompt 含证据全文，远大于探测请求，不能用 0.7s 推算）。
- 实际约束按优先级：① PG checkpointer 连接池 pool_min=5（20 图并发写 checkpoint 可能排队，必要时上调）；② httpx/MCP 网关连接池与 per-server QPS；③ 单条端到端延迟与降级率。
- 结论：并行度由压测拐点决定，候选 4→8→16，不预设 20。

### D8：长稳重启恢复机制
- 30 分钟满载中主动重启：未 ACK 的 RocketMQ 消息不可见超时后自动重投；PG checkpointer 使已到断点的图可 resume；`graph_lock`/在途守卫拦截重投重复执行；内存溢出队列依赖 D4 的关停重新入队。
- 观测：重启前后工单总数=完成数+人审数+在途数对账；资源（内存/TCP/PG/Redis key）曲线无单调增长；三表裁剪生效。

## Risks / Trade-offs

- [common 组件改动影响其他服务] → 并行度默认 1 且可配置，qc/dp/rag 回归其事件闭环。
- [证据节点 gather 异常放大/部分成功误判] → return_exceptions 逐一分流，保持 exception_info 降级契约，补并行节点单测。
- [连接池复用在特定 mcp SDK 版本下不安全] → D3 强制先做 SDK 文档 spike，冒烟验证连接数与错误映射，否则保留每次新建。
- [并行后 PG 连接池成瓶颈] → 压测监控 pool 等待，必要时上调 pool_min/max，纳入任务。
- [慢单超 300s 不可见窗口致二次拉取] → 图锁防重不产生重复结果，但浪费资源；按 D6 心跳续期或上调。
- [Redis 溢出队列与内存队列双队列一致性] → 仅在内存满时溢出、补偿统一走 acquire_complaint 收口；满→退避塞回防刷；关停重新入队。
- [模型黑盒路由 flash 与"reasoner"预期能力差异] → 本 change 不改，记录技术债；压测关注 JSON schema 符合率与降级率，异常升高则推动显式改模型名。

## Migration Plan

- 阶段顺序与依赖（与 tasks 一致）：D1 限流可配置 → D2/D3 MCP 并行与连接复用 → D4 Redis 溢出（开关默认关）→ D5/D6 事件消费并行（并行度默认 1）→ D7/D8 爬坡与长稳压测。
- 灰度：阶段1 环境变量默认 60；阶段3 溢出开关默认关；阶段4 并行度默认 1。三者均通过配置回到现状，无需回滚代码。
- 每阶段：单点冒烟 → 端到端 → pass/fail 对照表；存量 1402 单测全过 + 新增测试。

## Open Questions

- mcp SDK 锁定版本号与 `streamable_http_client` 复用外部 `httpx.AsyncClient` 的官方确认结论（D3 spike，动手编码前完成；若结论为不支持，则连接池复用降级为"仅复用 httpx.AsyncClient 到单次 session 边界外"或取消该项，不影响其余阶段）。
- PG checkpointer 连接池在并行度 16 下的实际等待数据（压测测定后决定是否上调 pool_min/max）。
- RocketMQ 5.x 当前使用的 Python 客户端对 `change_invisible_time` 心跳续期的支持情况（仅当压测出现慢单超 300s 时才需要落地）。
- **技术债（2.5 遗留）**：mcp 1.30.0 客户端 `_handle_sse_response`（SDK streamable_http.py:446-449）在收到响应事件后提前 `response.aclose()` 关闭 SSE 流，httpx 对提前关闭的流式响应不归还 keepalive 池 → TCP 层实际不跨调用复用（每次新建→用完即关，无泄漏、连接数稳定 0，实测 10 轮 ESTABLISHED=[0×10]）。连接池外壳（Limits 50/20、实例级 aclose、关停钩子）保留生效。真复用需改 mcp SDK 或网关响应封装，均超出本 change 白名单；收益仅约 1ms/次握手，已评审接受现状。后续可在 SDK 升级或网关改造时重估。

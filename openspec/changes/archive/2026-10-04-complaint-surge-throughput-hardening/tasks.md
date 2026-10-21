## 1. 阶段1：LLM 限流可配置化（前置）

- [x] 1.1 在公共层（`common/llm/reliability.py`）定义由环境变量 `LLM_RATE_LIMIT`/`LLM_RATE_WINDOW` 读取的限流配置，默认 60/60，作为单一事实源；移除模块内重复硬编码。验证：默认值单测断言 60/60
- [x] 1.2 `services/complaint-service/app/llm/real_transport.py` 改为引用 1.1 的同一配置，删除本地 `_LLM_RATE_LIMIT/_LLM_RATE_WINDOW` 重复常量。验证：grep 确认两处不再各自硬编码，且公共层与投诉 transport 读取同一来源
- [x] 1.3 保持限流 key（`{svc}:llm_ratelimit:{model}`）、超限抛 `LLMRateLimitError`、Redis 故障静默放行三条语义不变。验证：`common/llm` 与 complaint LLM 相关既有单测全绿
- [x] 1.4 在 `.env.example`/配置说明补充 `LLM_RATE_LIMIT`/`LLM_RATE_WINDOW`（默认不改变现状）。验证：不设置变量启动行为与改造前一致
- [x] 1.5 阶段1 回归：跑 complaint + common 存量单测。验证：全绿，输出本阶段 pass/fail 对照表

## 2. 阶段2：MCP 并行化 + 连接复用（低风险）

- [x] 2.1 SDK 前置 spike：核对项目锁定的 mcp SDK 版本官方文档，确认 `streamable_http_client` 复用外部长生命周期 `httpx.AsyncClient`（连接池）的连接生命周期与 stateless 行为；记录结论到实现说明。验证：产出明确结论（支持/不支持 + 依据），不支持则按 design D3 降级方案执行
- [x] 2.2 `evidence_case.py`：单 case 内 query_case_info/query_history_tickets/query_history_complaints 改 `asyncio.gather` 并发；逐异常分流，任一失败仍返回 `exception_info`。验证：新增单测断言三查询并发且单查询失败时写 exception_info、不产出判责
- [x] 2.3 `evidence_call_qc.py`：query_call_asr/query_qc_violations 改并发，异常契约一致。验证：新增单测断言并发与 ASR/QC 结果装配、异常降级不变
- [x] 2.4 校验并行下不突破 MCP per-server QPS（business_ticket=50/call_qc=30/rag_kb=20），必要时在节点并发处加受控信号量。验证：单测/冒烟观测未触发 MCP 限流拒绝
- [x] 2.5 `app/mcp/real_transport.py`：HTTP 客户端提为实例级长生命周期 keepalive 连接池（设连接数上限），MCP session 仍 stateless；main lifespan 关停 `aclose`。验证：新增/更新单测，错误映射（超时/401/403/HTTP/MCP）不变
- [x] 2.6 单点冒烟：单工单真实跑证据节点，记录耗时由串行降为约单次最慢查询；连跑多条观测 TCP 连接数不持续增长。验证：耗时与连接数证据入 pass/fail 对照表

## 3. 阶段3：HTTP 队列满溢出 Redis 延迟队列（独立分支，默认关）

- [x] 3.1 新增溢出开关配置（默认关闭）与 Redis List key、兜底容量上限配置。验证：默认关闭时现有 `B0006` 行为单测不变
- [x] 3.2 `rate_limiter`/judge 路由：开关开启且内存并发闸+队列满时，把 complaint_id LPUSH 入 Redis 延迟队列（FIFO），未超兜底容量返回 HTTP 202；回滚维5锁逻辑保持。验证：新增单测断言满时 202 + 入队，不返回 B0006
- [x] 3.3 延迟队列达兜底容量或开关关闭/Redis 故障时回退 B0006。验证：单测覆盖容量超限、Redis 不可用两条回退路径
- [x] 3.4 main lifespan 新增后台补偿循环：BRPOP 取单→重新 acquire_complaint（granted 执行 / queued 随内存队列 / 仍 full 带退避塞回），禁止无退避空转。验证：单测覆盖三种分支与退避塞回
- [x] 3.5 优雅关停：补偿循环取消时在途/积压重新入队或经既有重投恢复，保证不丢、最终只研判一次。验证：关停恢复单测（幂等锁/图锁/在途集合保证不重复）
- [x] 3.6 端到端冒烟：构造 >520 并发，断言溢出条进 Redis、资源释放后补位执行且仅一次。验证：pass/fail 对照表 + 幂等证据

## 4. 阶段4：事件消费并行（风险最高，依赖阶段1）

- [x] 4.1 `common/eventbus/rocketmq.py`：单批消息改 `asyncio.gather` 分派 + 可配置信号量限并行度，默认值 1（等同现状逐条串行 + 逐条 ACK）。验证：并行度=1 时既有 RocketMQ 消费单测全绿
- [x] 4.2 保证单条 handler 失败不影响同批、失败条目不 ACK（沿用 Broker 重投/DLQ），成功才 ACK。验证：新增单测——批内一条抛异常，其余确认、失败条进重试路径
- [x] 4.3 并行度作为适配器配置项透传，默认不改变 qc/data-platform/rag 消费行为；回归这些服务订阅链路。验证：qc/dp/rag 事件相关单测全绿
- [x] 4.4 评估并按需处理 `RECEIVE_INVISIBLE_DURATION=300s`：压测若出现慢单在途期间消息重回可见，落地心跳续期（change_invisible_time）或上调至与全局超时 1800s 对齐。验证：压测无意外二次拉取，或续期/上调配置生效
- [x] 4.5 事件入口 100 条批量小批量冒烟（先并行度 4），确认未 ACK 重投 + graph_lock/在途守卫拦截重复、无 checkpoint 分叉。验证：不丢不重冒烟证据（run9 并行度4：100/100 收敛、missing=0、dup_complaint=0、dup_session=0、checkpoint 无分叉，2026-10-04 回填）

## 5. 爬坡压测、长稳与全局验收

- [x] 5.1 用 `tests/loadtest` 以 100 条 `complaint.created` 分别在并行度 4/8/16 压测，采集 LLM 429/熔断次数、PG checkpointer 连接池(pool_min=5)等待、httpx/MCP 网关并发、单条端到端延迟、降级率。验证：三档对照表（同一100案 cases_tier_100.txt，run9/run10t8/run11t16，2026-10-04）——墙钟 26.0/25.5/25.1min；P50 814/916/871s、P95 1503/1517/1498s、P99 1509/1523/1505s；三档均 0丢0重、降级率0%、logmetrics（LLM限流/熔断/降级、MCP超时/限流/鉴权）全0、pg_active 恒=1 无池等待。结论：瓶颈为单卡 vLLM 吞吐而非消费侧
- [x] 5.2 依据 5.1 拐点确定最终并行度并落为配置值；若 PG 池等待明显，评估并上调 pool_min/max。验证：最终并行度=8 已落 .env（ROCKETMQ_CONSUMER_PARALLELISM=8）；4/8 吞吐持平但 8 给 MCP 证据并发与单案卡顿留重叠余量，16 无增益且受 common/eventbus rocketmq.py:191 receive(8) 批大小硬顶（实际在途恒=8）；PG pg_active 全程=1 无池等待，pool_min/max 不调；批大小硬顶登记技术债 README §8.2 #35。100 条全部完成、不丢不重（三档均证）
- [x] 5.3 长稳压测：最终并行度满载持续 ≥30 分钟，窗口内主动重启一次。验证：soak53 满载 1800s（03:17:44–03:48:09Z），滚动发布 248/248 成功；03:33:01Z（T+15min）窗口内 docker restart complaint-service，重启前快照 64 案（56 终态/8 在途/0 重复）→重启后 13s healthy、订阅重挂、消费恢复；04:03:06Z 全量收敛：published=248=complaints、terminal=248、inflight=0、missing=0、dup_complaint=0、dup_session=0，重启时刻 8 个在途案经未 ACK 重投消化且无重复研判
- [x] 5.4 长稳资源与劣化断言：内存/TCP/PG 连接/Redis key 不随时间持续增长（三表裁剪生效），超时异常单转 pending_review 不阻塞滚动，降级率与延迟无时间劣化。验证：sample_soak53.csv 35 点分段——RSS 爬坡162/稳态168/重启后163/尾段167MiB 稳态持平（重启归零，收敛后181为 Python arena 高水位不归还，非单调增长）；TCP est 稳态32→35 持平、收敛后回落22；pg_active 全程恒=1、pg_idle 稳态30-31 连接池复用；redis csmp 业务键稳态恒定17、收敛后归0（裁剪/TTL生效），cs:event_id 幂等键248个 TTL≈23h 有界；异常降级=0、滚动不阻塞；P50=723s 无处理劣化（P99=1767/max=1919 升高源于 inflight目标32>实际worker8 的有意积压排队，非处理变慢）。资源曲线 + 分段 pass 断言见 watch_1791086586/sample_soak53
- [x] 5.5 消息保留期断言：100 条滚动消化速率下队尾消息在 Broker 保留期内被确认。验证：Broker fileReservedTime=48h（172800s）；soak 端到端 max=1918.9s（队尾最长驻留），余量 170881s（约47.5h），无超期删除
- [x] 5.6 全局回归：存量 1402 单测全过 + 本 change 新增并行/溢出/长稳测试；四服务事件闭环（etl→qc→complaint→result）端到端通过。验证：全量 11 套件 1572 passed / 29 skipped / 0 failed（本 change 边界内 common 全套310、eventbus44、complaint399、rag372、qc371、dp16 全绿；mcp-gateway 53 经修复 Starlette 中间件签名 `asgi_app`→`app` + 宿主机装 prometheus_client 后全绿）。四服务闭环 run_min_loop：事件链 etl.task.done→qc.check.completed→complaint.created→complaint 消费建单→judge 跑图（real LLM invoke）→pending_review 全链路通，case_id 四处对齐、质检分数来自真开检均 PASS；`ads_case_loop.verdict` 列空系 `SIM_AUTO_REVIEW=true` 走 `sim_auto_review_skip_no_breakpoint` 跳过人工复核点不触发 sample_backflow 所致（存量配置行为，非事件并行化引入；soak 真实案 03:56 有 sample_backflow_ok 佐证 backflow 机制正常），登记技术债 README §8.2 #36
- [x] 5.7 回滚演练：分别以默认限流、溢出开关关、并行度=1 验证系统完全回到现状行为。验证：①默认限流 LLM_RATE_LIMIT=60：recreate 后 env=60、healthy，发 1 条投诉正常消费到 pending_review，Redis 限流键 csmp:llm_ratelimit:deepseek-chat 存在（机制工作）；②溢出开关关：COMPLAINT_DEFERRED_QUEUE_ENABLED 默认 false，test_deferred_queue.py:153 已断言默认 false 时 HTTP 满走 B0006 现状行为；③并行度=1：recreate 后 env=1、healthy，发 1 条投诉串行消费到 pending_review，judge_complete 日志 1 条无异常。三项均回到现状行为。已恢复最终定档配置（并行度=8、限流=60）
- [x] 5.8 技术债登记：在相关文档/backlog 记录 deepseek-r1/deepseek-reasoner 被服务端黑盒路由为 deepseek-flash（本 change 不改模型名）。验证：技术债条目可查（已登记至 README.md §8.2 P2 表 #25，2026-10-02）

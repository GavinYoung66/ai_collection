## Why

投诉研判事件入口（RocketMQ `complaint.created` → `judge_async(run_graph=True)`）对一批消息逐条串行处理（`common/eventbus/rocketmq.py` 的 `for + await`），每条跑图约 40s，真实批量消化慢；证据节点 MCP 调用串行且每次新建连接，进一步拉长单条耗时；HTTP 入口在五维限流并发闸(20)+队列(500)满后直接返回 `B0006(429)` 且无补偿，调用方不重试即丢单。需要在不改变业务语义（AI 仅出草稿、最终人审、降级一律 `pending_review`）的前提下，把系统稳定支撑能力提升到「同时 100 条投诉并发执行 + 磁盘队列滚动补位、不丢不重」，并补齐队列满丢单缺陷。

## What Changes

- **LLM 限流可配置化（前置）**：消除 `common/llm/reliability.py:141-142` 与 `services/complaint-service/app/llm/real_transport.py:29-30` 两处重复硬编码的 `_LLM_RATE_LIMIT=60/60s`，统一从环境变量 `LLM_RATE_LIMIT`/`LLM_RATE_WINDOW` 读取并收敛为单一事实源；默认值保持 60，行为不变。
- **证据节点 MCP 并行化**：`evidence_case.py:29-42`（3 个查询）与 `evidence_call_qc.py:44-53`（2 个查询）单 case 内无依赖调用由串行 `await` 改为 `asyncio.gather` 并行；保留「任一 MCP 失败即 `exception_info` 降级」语义；并行度受 per-server QPS（business_ticket=50 / call_qc=30 / rag_kb=20）约束。
- **MCP 连接复用**：`app/mcp/real_transport.py` 每次调用新建 `httpx.AsyncClient`+MCP session 改为实例级长生命周期 keepalive 连接池复用 TCP，进程关停 `aclose`；MCP session 仍遵循 stateless_http 语义。
- **HTTP 队列满溢出 Redis 延迟队列（缺陷补洞）**：`OUTCOME_FULL` 不再一律 429；开关开启时把 `complaint_id` 溢出写入 Redis List（FIFO + 兜底容量），返回 202，由 `main.py` lifespan 后台循环 `BRPOP` 滚动补位、退避塞回、优雅关停不丢在途；开关关闭时保持现有 429 行为。
- **事件消费并行**：`common/eventbus/rocketmq.py` 单批消息改 `asyncio.gather` 并发处理 + 可配置信号量限并行度（`=1` 退回串行）；依赖未 ACK 重投 + `graph_lock`/在途守卫保证不丢不重；核对 `RECEIVE_INVISIBLE_DURATION(300s)` 与消息保留期。
- **容量与长稳验收**：以 100 条批量为容量目标，并行度按 4→8→16 爬坡压测定拐点；新增最终并行度下满载 ≥30 分钟、期间主动重启一次的长稳压测，验证不丢、不重、资源不泄漏、异常单转人审不阻塞滚动。
- 记录技术债（本 change 不改模型名）：主模型 `deepseek-r1`/`deepseek-reasoner` 已被 DeepSeek 服务端黑盒路由为 `deepseek-flash`。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `complaint-service/rate-limit`: 五维限流队列满（`OUTCOME_FULL`）由直接 `B0006(429)` 改为开关控制下溢出到 Redis 延迟队列、202 受理、后台滚动补位，兜底容量超限才真拒绝；新增溢出消费与优雅关停要求。
- `platform/event-bus`: 新增 RocketMQ 单批消息有界并发消费要求（可配置并行度，`=1` 串行回滚），在 at-least-once 基础上明确并行下不丢、不重（幂等/图锁防 checkpoint 分叉）与 invisible duration/消息保留期匹配。
- `platform/llm-reliability`: LLM 模型维限流阈值改为环境变量可配置、单一事实源，默认值保持 60/60s，Redis 故障仍静默放行。
- `complaint-service/graph-nodes`: 证据采集节点（evidence_case / evidence_call_qc）单 case 内独立 MCP 查询并行化，结果与异常降级契约不变，并受 MCP per-server QPS 约束。
- `complaint-service/mcp-real-path`: real 模式 HTTP 传输改为实例级长生命周期 keepalive 连接池复用 TCP，关停释放连接；调用结果与错误映射契约不变。
- `complaint-service/load-testing`: 新增 100 条洪峰批量 + 最终并行度满载 ≥30 分钟且中途重启一次的长稳压测，与不丢/不重/资源不泄漏/降级率的 pass-fail 判定。

## Impact

- **代码**：
  - `common/llm/reliability.py`（限流阈值单一事实源、环境变量化；公共组件，须向后兼容）
  - `common/eventbus/rocketmq.py`（消费循环有界并行 + 并行度配置；公共组件，须回归 qc / data-platform / rag 订阅链路）
  - `services/complaint-service/app/llm/real_transport.py`（读取统一限流配置）
  - `services/complaint-service/app/graph/nodes/evidence_case.py`、`evidence_call_qc.py`（并行采集）
  - `services/complaint-service/app/mcp/real_transport.py`（连接池复用）
  - `services/complaint-service/app/api/routes/complaints.py`、`app/service/rate_limiter.py`、`app/main.py`（Redis 溢出队列与后台消费循环、开关）
  - `services/complaint-service/config`（新增环境变量与开关，默认保持现状）
- **不改动**：qc-service / rag-service / data-platform 业务代码；业务语义与四服务事件闭环（etl→qc→complaint→result）；不做 git commit。
- **依赖/中间件**：复用现有 Redis、PostgreSQL checkpointer、RocketMQ；MCP 连接复用实现前须核对项目内 mcp SDK 版本官方文档确认连接池复用安全性。
- **风险**：事件消费并行与 common 公共组件改动风险最高，放最后阶段，靠环境变量/开关灰度与 `=1` 串行回滚兜底。
- **验收基线**：存量 1402 单测全过 + 新增并行/溢出/长稳测试；每阶段单点冒烟→端到端，输出逐项 pass/fail 对照表。

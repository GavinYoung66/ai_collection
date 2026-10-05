## Purpose

为数据平台、质检服务、投诉服务三个子系统提供统一的事件总线能力：事件信封契约、物理 Topic 与逻辑事件名规范、多适配器抽象（Redis Pub/Sub 灰度、RocketMQ 5.x 目标、Redis Streams 降级兜底）、消费重试与死信队列、可观测埋点，使异步通知从 Redis Pub/Sub 升级为持久化、可重试、可追踪的统一事件通信基础设施。

## Requirements

### Requirement: 统一事件信封与 Topic 规范

系统 SHALL 使用统一事件信封。物理 Topic SHALL 为 `csmp_business_events`，逻辑事件名 SHALL 放消息 Tag 或属性。统一信封字段 SHALL 为 `{event_id, event_name, occurred_at, trace_id, data}`，`data` 字段仅允许白名单定位字段：`case_id`、`session_id`、`qc_record_id`、`complaint_no`、`backfill_date`。SHALL NOT 在 `data` 中携带正文、评分、对话、对话摘要、质检详情、RAG 结果、PII 或大对象。消息的双侧（发布与消费）SHALL 对 `data` 中的非白名单字段进行强校验并拒绝，且 SHALL 留下 structlog 审计记录（含 `event_id`、`trace_id`、拒绝原因）。

#### Scenario: 发布事件含合法 data

- **WHEN** 发布方构造 `complaint.created`，`data` 仅含 `complaint_no`
- **THEN** 事件信封通过校验并成功投递

#### Scenario: 发布事件含非法 data 被拒

- **WHEN** 发布方在 `data` 中携带 `conversation_text` 或 `score`
- **THEN** 事件被拒发，structlog 记录 `event_id`、`trace_id`、`rejected_field`

#### Scenario: 消费端收到含非法 data 的事件

- **WHEN** 消费端收到的事件 `data` 含非白名单字段
- **THEN** 消费端拒绝消费并记录 `event_id`、`trace_id`、`rejected_field`，SHALL NOT 进入业务 handler

### Requirement: 消费组命名规范

每个订阅方的消费组名 SHALL 为 `gid_{svc}_{event}` 格式，其中 `{svc}` 为子系统短码（`qc`、`dp`、`cs`），`{event}` 为逻辑事件名（以下划线连接）。同一消费组内的多副本 SHALL 负载均衡；不同消费组对同一事件 SHALL 独立消费。

#### Scenario: qc-service 订阅 call.session.ended

- **WHEN** qc-service 订阅 `call.session.ended`
- **THEN** 消费组名为 `gid_qc_call_session_ended`

### Requirement: 发布消费幂等

统一事件总线 SHALL 保证至少一次投递（at-least-once）。订阅方 handler SHALL 实现幂等消费。EventBus 抽象层 SHALL 暴露 `event_id` 供订阅方做去重。SHALL NOT 假设消息恰好投递一次。

#### Scenario: 同一 event_id 投递两次

- **WHEN** 网络抖动导致同一 `event_id` 的 `qc.check.completed` 投递两次
- **THEN** qc-service 消费端幂等处理，第二次投递不触发重复质检

### Requirement: 重试与死信队列

统一事件总线（RocketMQ）SHALL 配置消费重试次数为 3（含原始投递共 4 次）。超过重试上限后，消息 SHALL 进入死信队列（DLQ）。SHALL 在死信入队时记录告警日志（含 `event_id`、`event_name`、`consumer_group`、`trace_id`、`last_error`、`dlq_timestamp`）。SHALL 提供 `scripts/event_replay.py` 手动重放脚本，支持按 `event_id` 或时间窗口从 DLQ 重新投递到原 Topic。服务运行时 SHALL NOT 依赖 Redis Pub/Sub 进程内重试作为跨服务总线可靠性机制。

#### Scenario: rocketmq 模式消费失败 4 次入 DLQ

- **WHEN** handler 连续 4 次抛异常
- **THEN** 第 4 次后消息入 DLQ，告警日志含完整定位信息

#### Scenario: 从 DLQ 手动重放

- **WHEN** 运维人员执行 `python scripts/event_replay.py --event-id evt_xxx --dry-run`
- **THEN** 打印将要重放的消息数量和目标 Topic；去掉 `--dry-run` 后重放到原 Topic 并记录重放日志

#### Scenario: redis 模式无持久化 DLQ

- **WHEN** 配置 `EVENT_BUS=redis` 试图作为服务运行时总线
- **THEN** factory 抛出 `ValueError`，不提供 redis 总线适配器，因而也不提供 redis 进程内 DLQ 替代路径

### Requirement: 可观测与审计

所有发布和消费操作 SHALL 记录 structlog 审计日志。hooks 埋点名 SHALL 遵循 `{svc}.{component}.{action}` 规范：发布为 `csmp.eventbus.publish`，消费成功为 `csmp.eventbus.consume`，消费重试为 `csmp.eventbus.retry`，死信为 `csmp.eventbus.dlq`。每条日志 SHALL 携带 `event_id`、`event_name`、`trace_id`、`timestamp`、`svc`。消费重试日志 SHALL 额外携带 `attempt`、`max_attempt`、`error_type`。

#### Scenario: 发布事件记录审计日志

- **WHEN** data-platform 发布 `call.session.ended`
- **THEN** structlog 记录 `csmp.eventbus.publish`，含 `event_id`、`event_name`、`trace_id`、`svc=dp`

### Requirement: 环境切换与回滚

事件总线适配器 SHALL 由环境变量 `EVENT_BUS` 控制。服务运行时合法取值 SHALL 仅为 `rocketmq`。`EVENT_BUS` 缺失、为空、或为 `redis` / `redis_streams` / 其它非法值时，factory SHALL 抛出 `ValueError` 且不返回适配器。SHALL NOT 实现运行时双写。dp / qc / complaint / **rag** 的 compose（含 `docker-compose.complaint.yml` 等旁路）与根目录 `.env` SHALL 显式设置 `EVENT_BUS=rocketmq` 并传递 `ROCKETMQ_*`。单测 MAY mock `EventBus` Protocol。总线故障回滚 SHALL 通过修复 RocketMQ / DLQ 重放或代码回滚完成，SHALL NOT 通过改回 `EVENT_BUS=redis` 切换总线。

#### Scenario: 合法 rocketmq 取值

- **WHEN** `EVENT_BUS=rocketmq`
- **THEN** factory 返回 RocketMQ 适配器实例

#### Scenario: redis 取值被拒

- **WHEN** `EVENT_BUS=redis` 或 `EVENT_BUS=redis_streams`
- **THEN** factory 抛出 `ValueError`，不返回适配器实例

#### Scenario: 非法 EVENT_BUS 被拒

- **WHEN** `EVENT_BUS=foo` 或未设置
- **THEN** factory 抛出 `ValueError`，不返回适配器实例

#### Scenario: 从 rocketmq 回滚到 redis

- **WHEN** 运维人员试图把 `EVENT_BUS` 从 `rocketmq` 改回 `redis` 并重启服务（误当作总线回滚）
- **THEN** factory 抛出 `ValueError`，服务拒绝启动；**SHALL NOT** 以 Redis Pub/Sub 作为跨服务事件总线（场景名沿用主 spec 旧标题不可改——MODIFIED 块必须保留原场景名；语义为「禁止该回滚」）

#### Scenario: redis_streams 降级取值合法

- **WHEN** `EVENT_BUS=redis_streams`（旧降级取值）
- **THEN** factory 抛出 `ValueError`；**SHALL NOT** 再作为服务运行合法总线模式（场景名沿用主 spec 旧标题不可改；语义为拒绝该取值）

### Requirement: 跨服务业务事件仅经 RocketMQ 总线

data-platform、qc-service、complaint-service、**rag-service** 的跨服务业务异步通知 SHALL 仅通过统一 EventBus（RocketMQ）发布与订阅。SHALL NOT 使用 Redis Pub/Sub 或进程内存列表充当跨服务业务总线。Redis 限流键、幂等锁、`csmp:callback:dlq` List、`rag:sensitive_words:version` 等非总线用途 MAY 继续使用 Redis。

#### Scenario: 业务路径无 Redis Pub/Sub 跨服务事件

- **WHEN** 检查 dp/qc/complaint/rag 业务事件发布路径
- **THEN** 跨服务业务事件均经 `common/eventbus` RocketMQ 适配器

#### Scenario: complaint 业务 topic 上总线

- **WHEN** complaint-service 发布 `complaint.judged` 或 `complaint.result.ready`
- **THEN** 消息进入物理 Topic `csmp_business_events`（Tag=逻辑事件名）

### Requirement: 业务总线与韧性 hooks 双轨

跨服务业务逻辑事件 SHALL 经 EventBus 发布，信封 `data` 仅白名单定位字段。韧性/观测 hooks（名形如 `csmp.rate_limit.hit`、`csmp.circuit_breaker.open`、`csmp.degrade.triggered` 及同前缀变体）SHALL NOT 写入物理 Topic `csmp_business_events` 作为业务投递；SHALL 使用进程内可断言记录（或等价 tap）并/或 structlog；hooks 失败 MUST NOT 阻断主业务流程。

#### Scenario: 业务事件上总线

- **WHEN** 发布 `complaint.created` 或 `kb.sensitive_updated`
- **THEN** 消息经 EventBus 进入 `csmp_business_events`

#### Scenario: hooks 不上业务 Topic

- **WHEN** 限流命中触发 `csmp.rate_limit.hit`
- **THEN** 可在进程内 tap/structlog 观测，且 SHALL NOT 要求该事件通过白名单信封写入 `csmp_business_events`

### Requirement: RocketMQ 单批有界并发消费

统一事件总线的 RocketMQ 适配器 SHALL 支持把单次 `receive` 拉取到的一批消息以有界并发方式分派给 handler（而非强制逐条串行 await）。并发度 SHALL 可配置，且配置为 1 时 SHALL 退化为现有逐条串行行为（可回滚）。在并发分派下系统 SHALL 维持既有 at-least-once 语义：单条消息处理失败 SHALL NOT 影响同批其他消息，未成功处理的消息 SHALL NOT 被确认，依赖 Broker 重投与订阅方幂等（含 event_id 去重、业务幂等、图锁/在途守卫）保证不丢、不重、不产生同一 thread 的 checkpoint 分叉。消息不可见时长 SHALL 与并行下单条处理耗时及业务全局执行超时相匹配，避免消息在原处理仍在途时过早回到可见被二次拉取；系统 SHALL 保证在配置的目标滚动消化速率下，队尾消息不超过 Broker 消息保留期。并发度调整 SHALL NOT 要求修改订阅方 handler 代码。

#### Scenario: 并发度大于 1 时同批并行处理

- **WHEN** 一次 receive 拉取到多条消息且配置并发度 N>1
- **THEN** 最多 N 条消息的 handler 并发执行，其余等待信号量，全部成功后分别确认；总批处理耗时显著低于逐条串行

#### Scenario: 并发度为 1 退回串行

- **WHEN** 配置并发度=1
- **THEN** 适配器按原顺序逐条 await 处理并逐条确认，行为与改造前完全一致

#### Scenario: 单条失败不影响同批且不丢消息

- **WHEN** 同批中一条消息的 handler 抛异常，其余成功
- **THEN** 失败条目不被确认、由 Broker 按重试/DLQ 机制处理，其余条目正常确认，互不影响

#### Scenario: 并行重投不产生重复研判

- **WHEN** 同一 complaint.created 因不可见时长到期或重投被并发拉取两次
- **THEN** 订阅方幂等与图锁/在途守卫拦截第二次执行，不产生第二张图或 checkpoint 分叉

#### Scenario: 滚动消化不超消息保留期

- **WHEN** 以目标容量批量（100 条）持续投递
- **THEN** 队尾消息在 Broker 保留期到期前被消费确认，无因超期被删除导致的丢失

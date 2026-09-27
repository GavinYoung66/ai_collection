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

RocketMQ 模式 SHALL 配置消费重试次数为 3（含原始投递共 4 次）。超过重试上限后，消息 SHALL 进入死信队列（DLQ）。SHALL 在死信入队时记录告警日志（含 `event_id`、`event_name`、`consumer_group`、`trace_id`、`last_error`、`dlq_timestamp`）。SHALL 提供 `scripts/event_replay.py` 手动重放脚本，支持按 `event_id` 或时间窗口从 DLQ 重新投递到原 Topic。Redis 模式仅作灰度对照，SHALL 提供进程内重试（最多 3 次）+ structlog 告警，SHALL NOT 提供持久化 DLQ；此能力差异 SHALL 写入 spec。

#### Scenario: rocketmq 模式消费失败 4 次入 DLQ

- **WHEN** handler 连续 4 次抛异常
- **THEN** 第 4 次后消息入 DLQ，告警日志含完整定位信息

#### Scenario: 从 DLQ 手动重放

- **WHEN** 运维人员执行 `python scripts/event_replay.py --event-id evt_xxx --dry-run`
- **THEN** 打印将要重放的消息数量和目标 Topic；去掉 `--dry-run` 后重放到原 Topic 并记录重放日志

#### Scenario: redis 模式无持久化 DLQ

- **WHEN** EVENT_BUS=redis 且 handler 连续失败
- **THEN** 进程内重试 3 次后记录 structlog 告警，SHALL NOT 进入持久化 DLQ

### Requirement: 可观测与审计

所有发布和消费操作 SHALL 记录 structlog 审计日志。hooks 埋点名 SHALL 遵循 `{svc}.{component}.{action}` 规范：发布为 `csmp.eventbus.publish`，消费成功为 `csmp.eventbus.consume`，消费重试为 `csmp.eventbus.retry`，死信为 `csmp.eventbus.dlq`。每条日志 SHALL 携带 `event_id`、`event_name`、`trace_id`、`timestamp`、`svc`。消费重试日志 SHALL 额外携带 `attempt`、`max_attempt`、`error_type`。

#### Scenario: 发布事件记录审计日志

- **WHEN** data-platform 发布 `call.session.ended`
- **THEN** structlog 记录 `csmp.eventbus.publish`，含 `event_id`、`event_name`、`trace_id`、`svc=dp`

### Requirement: 环境切换与回滚

事件总线适配器 SHALL 由环境变量 `EVENT_BUS` 控制，取值 `redis`、`rocketmq` 或 `redis_streams`，重启生效：
- `redis`：Redis Pub/Sub 灰度对照（无持久化 DLQ）
- `rocketmq`：目标模式（arm64 spike go 路径）
- `redis_streams`：降级兜底（arm64 spike no-go 路径；消费组 + Streams）

非法取值 SHALL 在 factory 抛出 `ValueError`。SHALL NOT 实现运行时双写。回滚路径 SHALL 为：改 `EVENT_BUS=redis` → 重启服务 → 自动切回 Redis Pub/Sub 模式。三种模式 SHALL 使用同一套 `EventBus` 接口，业务代码 SHALL NOT 感知底层切换。

#### Scenario: 从 rocketmq 回滚到 redis

- **WHEN** 运维人员修改 `.env` 中 `EVENT_BUS=redis` 并重启服务
- **THEN** 业务事件发布/消费行为与切换前一致，无需改业务代码

#### Scenario: 非法 EVENT_BUS 被拒

- **WHEN** `EVENT_BUS=foo`
- **THEN** factory 抛出 `ValueError`，不返回适配器实例

#### Scenario: redis_streams 降级取值合法

- **WHEN** `EVENT_BUS=redis_streams`
- **THEN** factory 返回 Redis Streams 适配器（与 design Decision 6 no-go 路径一致）

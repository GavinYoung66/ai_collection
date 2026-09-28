## MODIFIED Requirements

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

## ADDED Requirements

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

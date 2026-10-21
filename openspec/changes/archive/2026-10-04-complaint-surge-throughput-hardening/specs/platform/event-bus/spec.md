## ADDED Requirements

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

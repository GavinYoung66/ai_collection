## MODIFIED Requirements

### Requirement: Pub-Sub `kb.sensitive_updated` 事件发布

系统 SHALL 在敏感词版本号变更（含 seed 初始化、手动 refresh 两种触发）后，经统一 EventBus（RocketMQ，物理 Topic `csmp_business_events`，Tag=`kb.sensitive_updated`）发布通知。SHALL NOT 使用 Redis PUBLISH 作为跨服务通知主路径。信封 SHALL 携带 `trace_id` 与 `occurred_at`；信封 `data` MAY 为空（SHALL NOT 把 `version`/正文塞进非白名单 `data` 字段；version 仍写 Redis 键 `rag:sensitive_words:version`）。消费方（qc-service 等）收到事件后 SHALL 在 60s 内触发词库热刷新（全量拉取或比对该 Redis version 键）。发布失败不影响 rag-service 主流程（warning 日志 + 降级为 version GET 轮询兜底）。rag-service 运行时 SHALL 使用 `EVENT_BUS=rocketmq`。启动自检日志键统一为 `sensitive_baseline_*` 系列（`sensitive_baseline_seed_success` / `sensitive_baseline_count_failed` / `sensitive_baseline_ok`），与代码实现一致。

#### Scenario: seed 初始化后发布 JSON 事件

- **WHEN** rag-service 首次启动 seed 写入 50 词并 version=1
- **THEN** 经 EventBus 发布 `kb.sensitive_updated`（信封含 `trace_id`）；Redis `rag:sensitive_words:version`=1；qc-service 于 60s 内触发热刷新

#### Scenario: 手动 refresh 后 JSON 事件递增

- **WHEN** ADMIN 通过内部 refresh 接口新增 1 条 active 词，当前 trace_id=trace_new01
- **THEN** 经 EventBus 发布 `kb.sensitive_updated`（`trace_id=trace_new01`）；Redis version 递增；qc-service 可刷新 AC

#### Scenario: Redis Pub-Sub 故障降级

- **WHEN** EventBus publish 因 RocketMQ/网络故障失败
- **THEN** rag-service 记录 warning 日志（warning 级别），不阻断对外服务接口，不返回错误给管理后台调用方（后台/消费方依赖 `rag:sensitive_words:version` GET 轮询兜底）

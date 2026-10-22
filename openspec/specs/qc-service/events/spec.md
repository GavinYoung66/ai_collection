## Purpose

定义 qc-service M1 Redis 发布订阅：六个发布频道及冻结 payload、四个订阅，以及敏感词热刷新；不含实时 escalate 或 `qc.llm.degraded` 频道。

## Requirements

### Requirement: 恰好发布六个频道

qc-service SHALL 发布下列逻辑事件，且不发布 `qc.escalate.human`、不发布 `qc.llm.degraded` 逻辑事件。每条业务 payload 构造 MAY 含 Unix `ts` 与 `trace_id` 等完整字段供本服务日志与断言；上统一事件总线时信封 `data` SHALL 仅含白名单定位字段。六个逻辑事件 SHALL 全部经统一事件总线（物理 Topic `csmp_business_events`，逻辑事件名放 Tag）发布。SHALL NOT 对上述六事件使用 Redis PUBLISH 作跨服务投递。本 change 对六出站仅保证投递契约；SHALL NOT 要求本 change 内必须存在跨服务消费者，也 SHALL NOT 仅为告警详情新建反拉 API。

| 逻辑事件名 | 业务构造键（上总线前可完整构造） |
| :--- | :--- |
| `qc.alert.raised` | `{alert_id,session_id,call_id,level,evidence,trace_id,ts}` 并 XADD `alert:stream` |
| `qc.check.completed` | `{qc_record_id,session_id,call_id,case_id,score,deduction,risk,grade,trace_id,ts}` |
| `qc.complaint.suspected` | `{qc_record_id,session_id,case_id,redline_tags[],trace_id,ts}` |
| `qc.stop_call.detected` | `{session_id,case_id,evidence,trace_id,ts}` |
| `qc.review.done` | `{qc_record_id,action,revised_score,redline_tags,evidence_masked,sample_type,trace_id,ts}`，`sample_type∈{positive,negative,corrected}` |
| `system.alert.raised` | `{alert_id,level,event,service,description,error_code,trace_id,ts}`，LLM 熔断时 `event=qc:llm_degraded` |

`qc.check.completed.deduction` SHALL 由库 `deduction_total` 映射，为主口径。`qc.check.completed.score` SHALL 由库 `compliance_score` 映射（派生兼容值，灰度期保留）。`risk` SHALL 由库 `risk_level` 映射。`grade` SHALL 恒为 JSON null。JSON MUST 使用键 `deduction`/`score`/`risk`，不是列名。`deduction` 与 `score` 均为可选键，消费方 MUST NOT 依赖其必然存在。`qc_result`/`qc_status` MUST NOT 出现在 completed payload。`qc.check.completed.call_id` SHALL 存在，且为 `call_records.id` / `QCSession.call_id` 的 UUID 字符串或 JSON `null`；MUST NOT 复制 `session_id` 或 replay 路径参数。上总线时 deduction/score/risk/grade/evidence 等非白名单字段 SHALL NOT 进入信封 `data`；消费方若存在则 HTTP 反拉详情。

`qc.alert.raised` SHALL 仅在 MergeHits 终态 `speaker=agent` 且 `severity=high` 时发布（与 `qc_alerts` 同门槛）。LLM 熔断的 `system.alert.raised` SHALL 设 `event=qc:llm_degraded` 且 `error_code=A2011`。服务 MUST NOT 发布 `qc.llm.degraded`。

#### Scenario: completed 使用 score 与 risk 键

- **WHEN** 质检跑完且 `deduction_total=20`
- **THEN** 发布前构造的业务 payload 含 `deduction=20`（由 `deduction_total` 映射）、`score=80`（由 `compliance_score` 映射）、`risk` 与 `grade=null`，且 `call_id` 为 UUID 字符串或 JSON null；上总线时仅将白名单定位字段写入信封 `data`

#### Scenario: 六事件均走 EventBus

- **WHEN** 质检路径发布 `qc.alert.raised` 或 `qc.review.done`
- **THEN** 消息经统一 EventBus（RocketMQ）投递，不经 Redis PUBLISH 跨服务投递

#### Scenario: 信封强校验生效

- **WHEN** 发布任一六事件时信封 `data` 含非白名单字段（如 `deduction`、`score` 或 `evidence`）
- **THEN** 事件总线拒绝发布并记录 `event_id`、`trace_id`、`rejected_field`

#### Scenario: 永不发布 escalate

- **WHEN** 任意 M1 质检路径完成（含坐席高危）
- **THEN** 订阅 `qc.escalate.human` 收不到消息

#### Scenario: LLM 降级走 system.alert.raised

- **WHEN** LLM 熔断打开
- **THEN** 发布 `system.alert.raised` 且 `event=qc:llm_degraded`、`error_code=A2011`，且不使用逻辑事件 `qc.llm.degraded`

### Requirement: 韧性 hooks 事件带 qc 前缀且不进六频道白名单

qc-service 启动 lifespan SHALL 调用 `hooks.set_publisher` 注入本服务适配器。shutdown MUST 调用 `hooks.set_publisher(None)`，避免测试间泄漏。韧性事件名 SHALL 为 `qc.rate_limit.hit`、`qc.circuit_breaker.open`、`qc.degrade.triggered`（及零件发出的同前缀变体，如 `qc.rate_limit.degraded`）。有请求上下文时 payload SHALL 携带 `trace_id`（限流路径经 `allow(..., context=)` 透传），并含 `reason` / `key` 等零件字段。适配器 MUST NOT 将这些事件 PUBLISH 到 M1 六个逻辑事件白名单，MUST NOT 发布 `qc.llm.degraded`。LLM 熔断业务告警 SHALL 仍走既有 `system.alert.raised`（`event=qc:llm_degraded`，`error_code=A2011`）。适配器或下游失败 MUST NOT 抛异常阻断质检主流程。

#### Scenario: 限流命中发出 qc.rate_limit.hit

- **WHEN** raw 限流超限
- **THEN** 钩子发出 `qc.rate_limit.hit`，且事件总线中该事件不进六频道白名单

#### Scenario: LLM 熔断仍走 system.alert.raised

- **WHEN** LLM 熔断打开
- **THEN** 仍发布 `system.alert.raised` 且 `event=qc:llm_degraded`、`error_code=A2011`；同时可发出 `qc.circuit_breaker.open` 或 `qc.degrade.triggered` 作为韧性观测，但不使用逻辑事件 `qc.llm.degraded`

#### Scenario: shutdown 注销 hooks publisher

- **WHEN** 服务进程关闭或测试 teardown 调用 shutdown
- **THEN** hooks publisher 被置为 None，后续事件回退 structlog，不阻断、不泄漏到下一用例

### Requirement: suspected 白名单与 tag 无关

`qc.complaint.suspected` SHALL 仅当合并 hits 存在 `speaker=agent` 且 `severity=high` 且 `rule_id∈{R11,R12,R13}` 时发布。R05 即使 `redline_tag=threat` MUST NOT 发布 suspected。R01、R10、用户投诉/自杀提及 MUST NOT 发布 suspected。

`qc.stop_call.detected` SHALL 当合并 hits 含坐席 R13 且为停呼场景时发布（用户表达停止致电/不要再打且坐席继续催收）。MUST NOT 仅因用户原话发布。

#### Scenario: R12 high 发布 suspected

- **WHEN** 合并 hits 含坐席 R12 high
- **THEN** 发布 `qc.complaint.suspected` 且含 `qc_record_id`

#### Scenario: R05 不发 suspected

- **WHEN** 仅有 R05 hits
- **THEN** 不发布 `qc.complaint.suspected`

#### Scenario: 停呼 R13 发布 stop_call.detected

- **WHEN** 用户说不要再打了且坐席继续催收从而命中 R13
- **THEN** 发布 `qc.stop_call.detected` 且含 `session_id` 与 `case_id`

### Requirement: 订阅四个频道

qc-service SHALL 通过统一事件总线（RocketMQ）订阅以下逻辑事件。订阅 handler SHALL 从信封中提取 `data`，根据 `event_name` 分发到现有处理逻辑。SHALL NOT 对下列入站事件使用独立于 EventBus 的 Redis 旁路订阅作为服务运行路径。`kb.sensitive_updated` 由 rag-service 经同一 EventBus 发布（见 `rag-service/sensitive-words-sync`）。`strategy.updated`：本仓若无发布方，qc 仍 SHALL 具备 EventBus 订阅就绪；任何未来发布方 MUST 经 EventBus。

| 逻辑事件名 | Handler 行为 |
| :--- | :--- |
| `call.session.ended` | 从 `data.session_id` 获取，GET qc-source；未齐 → Redis WAIT_TEXT，不插表 |
| `etl.task.done` | 从 `data.backfill_date` 获取，GET pending-qc，逐条 qc-source 补检 |
| `kb.sensitive_updated` | 加载敏感词快照到内存（可对照 Redis version 键） |
| `strategy.updated` | 刷新策略配置缓存 |

缺 `call.session.ended` 时服务 SHALL 仍能从 `etl.task.done` 或 HTTP replay 开工（M1）。data-platform MUST NOT HTTP 调用 qc。

#### Scenario: ended 但全文未齐则等待

- **WHEN** 收到 `call.session.ended` 且 qc-source 全文未齐
- **THEN** 写 Redis WAIT_TEXT，不插 `qc_records`

#### Scenario: 收到 call.session.ended 反拉数据

- **WHEN** qc-service 订阅器收到 `call.session.ended`
- **THEN** 从信封 `data.session_id` 获取，调用 `GET /api/v1/data/sessions/{session_id}/qc-source`，执行离线质检

#### Scenario: kb 与 strategy 经同一 EventBus

- **WHEN** 发布方经 EventBus 投递 `kb.sensitive_updated` 或 `strategy.updated`
- **THEN** qc-service 经统一 EventBus 消费并刷新，不依赖 Redis Pub/Sub 旁路作为运行主路径

### Requirement: WordLoader 全量刷新

WordLoader SHALL 在启动、每小时、以及 `kb.sensitive_updated` 时拉取 `current.dict`，重建 AC，并写 `qc:words:version`。MUST NOT 使用 `since_v` 增量接口。

#### Scenario: 词库更新重建 AC

- **WHEN** 收到 `kb.sensitive_updated`
- **THEN** 全量拉取 current.dict 并更新 `qc:words:version`

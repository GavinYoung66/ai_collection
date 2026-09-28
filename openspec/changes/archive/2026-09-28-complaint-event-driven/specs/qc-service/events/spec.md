## MODIFIED Requirements

### Requirement: 恰好发布六个频道

qc-service SHALL 发布下列逻辑事件，且不发布 `qc.escalate.human`、不发布 `qc.llm.degraded` 逻辑事件。每条业务 payload 构造 MAY 含 Unix `ts` 与 `trace_id` 等完整字段供本服务日志与断言；上统一事件总线时信封 `data` SHALL 仅含白名单定位字段。六个逻辑事件 SHALL 全部经统一事件总线（物理 Topic `csmp_business_events`，逻辑事件名放 Tag）发布。SHALL NOT 对上述六事件使用 Redis PUBLISH 作跨服务投递。本 change 对六出站仅保证投递契约；SHALL NOT 要求本 change 内必须存在跨服务消费者，也 SHALL NOT 仅为告警详情新建反拉 API。

| 逻辑事件名 | 业务构造键（上总线前可完整构造） |
| :--- | :--- |
| `qc.alert.raised` | `{alert_id,session_id,call_id,level,evidence,trace_id,ts}` 并 XADD `alert:stream` |
| `qc.check.completed` | `{qc_record_id,session_id,call_id,case_id,score,risk,grade,trace_id,ts}` |
| `qc.complaint.suspected` | `{qc_record_id,session_id,case_id,redline_tags[],trace_id,ts}` |
| `qc.stop_call.detected` | `{session_id,case_id,evidence,trace_id,ts}` |
| `qc.review.done` | `{qc_record_id,action,revised_score,redline_tags,evidence_masked,sample_type,trace_id,ts}`，`sample_type∈{positive,negative,corrected}` |
| `system.alert.raised` | `{alert_id,level,event,service,description,error_code,trace_id,ts}`，LLM 熔断时 `event=qc:llm_degraded` |

`qc.check.completed.score` SHALL 由库 `compliance_score` 映射。`risk` SHALL 由库 `risk_level` 映射。JSON MUST 使用键 `score`/`risk`，不是列名。`qc_result`/`qc_status` MUST NOT 出现在 completed payload。`qc.check.completed.call_id` SHALL 存在，且为 `call_records.id` / `QCSession.call_id` 的 UUID 字符串或 JSON `null`；MUST NOT 复制 `session_id` 或 replay 路径参数。上总线时 score/risk/grade/evidence 等非白名单字段 SHALL NOT 进入信封 `data`；消费方若存在则 HTTP 反拉详情。

`qc.alert.raised` SHALL 仅在 MergeHits 终态 `speaker=agent` 且 `severity=high` 时发布（与 `qc_alerts` 同门槛）。LLM 熔断的 `system.alert.raised` SHALL 设 `event=qc:llm_degraded` 且 `error_code=A2011`。服务 MUST NOT 发布 `qc.llm.degraded`。

#### Scenario: completed 使用 score 与 risk 键

- **WHEN** 质检跑完
- **THEN** 发布前构造的业务 payload 含由 `compliance_score`/`risk_level` 映射的 `score` 与 `risk`，且 `call_id` 为 UUID 字符串或 JSON null；上总线时仅将白名单定位字段写入信封 `data`

#### Scenario: 六事件均走 EventBus

- **WHEN** 质检路径发布 `qc.alert.raised` 或 `qc.review.done`
- **THEN** 消息经统一 EventBus（RocketMQ）投递，不经 Redis PUBLISH 跨服务投递

#### Scenario: 信封强校验生效

- **WHEN** 发布任一六事件时信封 `data` 含非白名单字段（如 `score` 或 `evidence`）
- **THEN** 事件总线拒绝发布并记录 `event_id`、`trace_id`、`rejected_field`

#### Scenario: 永不发布 escalate

- **WHEN** 任意 M1 质检路径完成（含坐席高危）
- **THEN** 订阅 `qc.escalate.human` 收不到消息

#### Scenario: LLM 降级走 system.alert.raised

- **WHEN** LLM 熔断打开
- **THEN** 发布 `system.alert.raised` 且 `event=qc:llm_degraded`、`error_code=A2011`，且不使用逻辑事件 `qc.llm.degraded`

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

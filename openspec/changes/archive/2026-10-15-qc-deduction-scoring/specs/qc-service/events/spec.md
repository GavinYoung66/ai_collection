## MODIFIED Requirements

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

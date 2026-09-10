## Purpose

定义 qc-service M1 Redis 发布订阅：六个发布频道及冻结 payload、四个订阅，以及敏感词热刷新；不含实时 escalate 或 `qc.llm.degraded` 频道。

## ADDED Requirements

### Requirement: 恰好发布六个频道

qc-service SHALL 发布下列频道，且不发布 `qc.escalate.human`、不发布 `qc.llm.degraded` 频道。每条 payload SHALL 含 Unix `ts` 与 `trace_id`。`call_id` SHALL 为 `QCSession.call_id` UUID 字符串或 JSON null；MUST NOT 复制 `session_id` 或 replay 路径参数。

| 频道 | Payload 键 |
| :--- | :--- |
| `qc.alert.raised` | `{alert_id,session_id,call_id,level,evidence,trace_id,ts}` 并 XADD `alert:stream` |
| `qc.check.completed` | `{qc_record_id,session_id,call_id,case_id,score,risk,grade,trace_id,ts}` |
| `qc.complaint.suspected` | `{qc_record_id,session_id,case_id,redline_tags[],trace_id,ts}` |
| `qc.stop_call.detected` | `{session_id,case_id,evidence,trace_id,ts}` |
| `qc.review.done` | `{qc_record_id,action,revised_score,redline_tags,evidence_masked,sample_type,trace_id,ts}`，`sample_type∈{positive,negative,corrected}` |
| `system.alert.raised` | `{alert_id,level,event,service,description,error_code,trace_id,ts}`，LLM 熔断时 `event=qc:llm_degraded` |

`qc.check.completed.score` SHALL 由库 `compliance_score` 映射。`risk` SHALL 由库 `risk_level` 映射。JSON MUST 使用键 `score`/`risk`，不是列名。`qc_result`/`qc_status` MUST NOT 出现在 completed payload。`qc.check.completed.call_id` SHALL 存在，且为 `call_records.id` / `QCSession.call_id` 的 UUID 字符串或 JSON `null`；MUST NOT 复制 `session_id` 或 replay 路径参数。

`qc.alert.raised` SHALL 仅在 MergeHits 终态 `speaker=agent` 且 `severity=high` 时发布（与 `qc_alerts` 同门槛）。LLM 熔断的 `system.alert.raised` SHALL 设 `event=qc:llm_degraded` 且 `error_code=A2011`。服务 MUST NOT 发布 `qc.llm.degraded`。

#### Scenario: completed 使用 score 与 risk 键

- **WHEN** 质检跑完
- **THEN** `qc.check.completed` 含由 `compliance_score`/`risk_level` 映射的 `score` 与 `risk`，且 `call_id` 为 UUID 字符串或 JSON null（绝不是 replay 的 `session_id`）

#### Scenario: 永不发布 escalate

- **WHEN** 任意 M1 质检路径完成（含坐席高危）
- **THEN** 订阅 `qc.escalate.human` 收不到消息

#### Scenario: LLM 降级走 system.alert.raised

- **WHEN** LLM 熔断打开
- **THEN** 发布 `system.alert.raised` 且 `event=qc:llm_degraded`、`error_code=A2011`，且不使用频道 `qc.llm.degraded`

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

服务 SHALL 订阅：

| 频道 | 处理 |
| :--- | :--- |
| `call.session.ended` | GET qc-source；未齐 → Redis WAIT_TEXT，不插表 |
| `etl.task.done` | GET pending-qc，再按 id 调 qc-source |
| `kb.sensitive_updated` | 全量重载词库 |
| `strategy.updated` | 刷新规则/策略缓存 |

缺 `call.session.ended` 时服务 SHALL 仍能从 `etl.task.done` 或 HTTP replay 开工（M1）。data-platform MUST NOT HTTP 调用 qc。

#### Scenario: ended 但全文未齐则等待

- **WHEN** 收到 `call.session.ended` 且 qc-source 全文未齐
- **THEN** 写 Redis WAIT_TEXT，不插 `qc_records`

### Requirement: WordLoader 全量刷新

WordLoader SHALL 在启动、每小时、以及 `kb.sensitive_updated` 时拉取 `current.dict`，重建 AC，并写 `qc:words:version`。MUST NOT 使用 `since_v` 增量接口。

#### Scenario: 词库更新重建 AC

- **WHEN** 收到 `kb.sensitive_updated`
- **THEN** 全量拉取 current.dict 并更新 `qc:words:version`

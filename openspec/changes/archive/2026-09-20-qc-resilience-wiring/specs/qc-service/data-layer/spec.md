## MODIFIED Requirements

### Requirement: WAIT_TEXT 只写 Redis

WAIT_TEXT SHALL 只写入 Redis 键 `qc:job:{session_id}`，JSON 含 `status` 及可选 `qc_record_id` / `skipped_rules[]`，TTL 24h，Redis DB 3。WAIT_TEXT MUST NOT INSERT `qc_records`。Redis 任务 `status` MUST 仅为 `WAIT_TEXT|RUNNING|DONE`。Redis `DONE` SHALL 表示任务结束（含库 `qc_status` 为 `PENDING_REVIEW` 或 `PARTIAL`）；Redis MUST NOT 把 `PENDING_REVIEW`、`REVIEWED`、`PARTIAL` 或 `WAIT_TEXT` 存为数据库 `qc_status`。库终态写入 SHALL 仅使用 `qc_status∈{DONE,PENDING_REVIEW,REVIEWED,PARTIAL}`。

qc-service MUST NOT INSERT 或 UPSERT `call_records` 或 `dialog_turns`。额外 Redis 键 SHALL 包括 `qc:words:version`、`qc:rules:active`、`qc:replay:run:{session_id}`、`qc:llm_degraded:{model}`（STRING `"1"`，TTL 60s，由 `invoke_llm` / `mark_degraded_flag` 按模型写入，到期自动恢复）。系统 MUST NOT 再写入或查询全局键 `qc:llm_degraded`（原 SET/DEL、无固定 EXPIRE 口径废弃）。

#### Scenario: 文本未齐不插行

- **WHEN** 通话已结束但全文未齐
- **THEN** Redis `qc:job` 为 WAIT_TEXT，且不存在 `qc_records` 行

#### Scenario: 重试耗尽落 PARTIAL

- **WHEN** `QC_TEXT_READY_RETRY_MAX` 耗尽
- **THEN** 系统 INSERT/UPDATE `qc_records.qc_status=PARTIAL` 并返回 A2003

#### Scenario: Redis DONE 不是库 PENDING_REVIEW

- **WHEN** 跑完且 agent+high（`qc_status=PENDING_REVIEW`）
- **THEN** Redis `qc:job.status` 为 `DONE`（任务结束），MUST NOT 等于 `PENDING_REVIEW`

#### Scenario: 熔断只写带模型后缀的键

- **WHEN** LLM 复核失败触发熔断
- **THEN** Redis 出现 `qc:llm_degraded:{model}` 且 TTL 为 60s 量级；全局键 `qc:llm_degraded` MUST NOT 被 SET

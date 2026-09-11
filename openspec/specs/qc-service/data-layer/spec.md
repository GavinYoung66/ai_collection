## Purpose

定义 qc-service M1 持久化：五张 PostgreSQL 表、Redis `qc:job`、skip 审计、v1.0 规则种子、rag 词库客户端，以及只读消费 data-platform `qc-source` / `pending-qc` 与夹具。

## Requirements

### Requirement: 五表及冻结列名

系统 SHALL 持久化 `qc_records`、`qc_violations`、`qc_alerts`、`qc_reviews`、`qc_rules`，对齐 step4/01 DDL。`qc_records.session_id` MUST 为 `UNIQUE REFERENCES call_records(session_id)`，且 MUST NOT 删除。MUST NOT 存在 `quality_id` 列。对外主键名 SHALL 为 `qc_record_id` = `qc_records.id`。

库列 SHALL 保持 `compliance_score`、`risk_level`、`qc_result`、`grade`。系统 MUST NOT 把这些列改名为 `score` 或 `risk`。HTTP 与事件 JSON 键仅为序列化层（DD-02 §4.0.1.1 / step5/02 §3.5）：HTTP 使用 `compliance_score` / `risk` / `qc_result` / `qc_status` / `grade`；`qc.check.completed` 使用 `score` / `risk` / `grade`。HTTP MUST NOT 用 `score` 作为合规分字段。事件 JSON MUST NOT 使用 `risk_level` 或 `compliance_score` 作为键名。

`qc_result` MUST 为 `compliant|violation|pending_review` 之一。`qc_status` MUST 为 `DONE|PENDING_REVIEW|REVIEWED|PARTIAL` 之一。`qc_status` MUST NOT 存储 `WAIT_TEXT` 或 `FAILED`。`grade` 有值时 MUST 为 `EXCELLENT|PASS|WARNING|SERIOUS` 之一。`qc_alerts.level` MUST 仅为 `high`。表 MUST NOT 增加 `skipped_rules` 列。

#### Scenario: 保留 session_id 唯一外键

- **WHEN** Alembic 创建质检表
- **THEN** `qc_records.session_id` 为 UNIQUE 且 REFERENCES `call_records(session_id)`

#### Scenario: 列名不是 HTTP 别名

- **WHEN** 检查 `qc_records` DDL/ORM
- **THEN** 列为 `compliance_score` 与 `risk_level`，不是 `score` 或 `risk`

#### Scenario: HTTP 不把 score 当库列

- **WHEN** 从行序列化 records HTTP body
- **THEN** JSON 使用 `compliance_score` 与 `risk`；ORM/表仍为 `compliance_score` 与 `risk_level`

### Requirement: WAIT_TEXT 只写 Redis

WAIT_TEXT SHALL 只写入 Redis 键 `qc:job:{session_id}`，JSON 含 `status` 及可选 `qc_record_id` / `skipped_rules[]`，TTL 24h，Redis DB 3。WAIT_TEXT MUST NOT INSERT `qc_records`。Redis 任务 `status` MUST 仅为 `WAIT_TEXT|RUNNING|DONE`。Redis `DONE` SHALL 表示任务结束（含库 `qc_status` 为 `PENDING_REVIEW` 或 `PARTIAL`）；Redis MUST NOT 把 `PENDING_REVIEW`、`REVIEWED`、`PARTIAL` 或 `WAIT_TEXT` 存为数据库 `qc_status`。库终态写入 SHALL 仅使用 `qc_status∈{DONE,PENDING_REVIEW,REVIEWED,PARTIAL}`。

qc-service MUST NOT INSERT 或 UPSERT `call_records` 或 `dialog_turns`。额外 Redis 键 SHALL 包括 `qc:words:version`、`qc:rules:active`、`qc:replay:run:{session_id}`、`qc:llm_degraded`（SET/DEL，除熔断策略外无固定 EXPIRE）。

#### Scenario: 文本未齐不插行

- **WHEN** 通话已结束但全文未齐
- **THEN** Redis `qc:job` 为 WAIT_TEXT，且不存在 `qc_records` 行

#### Scenario: 重试耗尽落 PARTIAL

- **WHEN** `QC_TEXT_READY_RETRY_MAX` 耗尽
- **THEN** 系统 INSERT/UPDATE `qc_records.qc_status=PARTIAL` 并返回 A2003

#### Scenario: Redis DONE 不是库 PENDING_REVIEW

- **WHEN** 跑完且 agent+high（`qc_status=PENDING_REVIEW`）
- **THEN** Redis `qc:job.status` 为 `DONE`（任务结束），MUST NOT 等于 `PENDING_REVIEW`

### Requirement: skipped_rules 耐久且不加列

系统 MUST NOT 给 `qc_records` 加 `skipped_rules` 列，MUST NOT 为此改 CHECK 枚举。任务终态（DONE / PENDING_REVIEW / PARTIAL）时系统 SHALL 写 `audit_logs`：`action=qc.skip.recorded`，`resource_type=qc_records`，`resource_id=qc_record_id`，`detail.skipped_rules`（空数组也写）。GET 详情回源顺序 SHALL 为 Redis `qc:job` → `audit_logs` → `[]`。

#### Scenario: 空 skipped_rules 仍审计

- **WHEN** 任务结束且无 skip 规则
- **THEN** `audit_logs` 仍有 `qc.skip.recorded` 且 `skipped_rules=[]`

#### Scenario: Redis 删除后仍返回 skipped_rules

- **WHEN** DEL `qc:job:{session_id}` 后再 GET 详情
- **THEN** `skipped_rules` 从 `audit_logs` 回源

### Requirement: 规则种子 v1.0 含 R14 十八词

系统 SHALL 种子 `qc_rules.version=v1.0` 为默认 active 包：penalty high=20 / medium=8 / low=2，`same_word_window_ms=30000`，grade 阈值 EXCELLENT=90 / PASS=80 / WARNING=60 / SERIOUS=0。R14 `params.keywords` MUST 恰好为这 18 个字符串：`起诉`、`立案`、`强制执行`、`拘留`、`坐牢`、`判刑`、`刑事责任`、`逮捕`、`征信花了`、`征信洗白`、`上黑名单`、`失信被执行`、`限制高消费`、`追究法律责任`、`冻结银行卡`、`影响子女`、`断供坐牢`、`无法贷款`。种子 MUST NOT 含 `我是法院的`。全库 MUST 仅一版 active。

#### Scenario: 默认 R14 关键词为十八词

- **WHEN** v1.0 为 active
- **THEN** R14 关键词等于该十八词列表，生产环境 R14 不会因空词而 skip

### Requirement: rag 客户端只拉 current.dict

系统 SHALL GET `{QC_RAG_SENSITIVE_BASE}/api/v1/sensitive-words/current.dict`，`text/plain`。超时 SHALL 为 3s，重试 3×，连续 2 次失败熔断 SHALL 保持上一版 AC 自动机。M1 MUST NOT 调用 `POST /kb/search` 或任何知识库检索 HTTP。`QC_R14_RAG_ENABLED` MUST 保持 false。

#### Scenario: 词库拉取失败保持旧 AC

- **WHEN** current.dict 连续超时两次
- **THEN** 继续使用上一版 AC 自动机，且不发起 `/kb/search`

### Requirement: DP qc-source 与 pending-qc 只读客户端

qc-service SHALL 仅作为 HTTP 客户端消费 data-platform：

- `GET {QC_DP_BASE}/api/v1/data/sessions/{session_id}/qc-source`（超时 5s，重试 3×）
- `GET {QC_DP_BASE}/api/v1/data/sessions:pending-qc?etl_date=&limit=`（超时 5s，重试 3×）

字段映射 MUST 逐行遵循 DD-02 §4.6.1。`result` MUST 出键（允许 JSON null）。缺失 `dial_history_24h` SHALL 整段省略（R05 skip）。M1 下 `ptp_amount` 与 `standard_answer_hit` SHALL 为 JSON null。qc MUST NOT 反查 `cases`。data-platform MUST NOT HTTP 回调 qc。pending-qc SHALL 只返回 `session_ids[]`；每个 id SHALL 再调 qc-source。pending-qc 熔断 SHALL 跳过本轮补检。

本 change MUST NOT 实现 data-platform ETL 或宽表。本 change MUST NOT 实现 call-service。qc-service MUST NOT HTTP 调用 call-service。

#### Scenario: qc-source 映射夹具

- **WHEN** 加载 qc-source 夹具
- **THEN** 含 `result`（可空）；聚合失败时省略 `dial_history_24h`；`ptp_amount`/`standard_answer_hit` 为 null

#### Scenario: 本 change 无 DP ETL

- **WHEN** 实现本 change
- **THEN** qc-service 下未增加 data-platform ETL/宽表代码

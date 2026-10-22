## Purpose

定义 qc-service M1 持久化：五张 PostgreSQL 表、Redis `qc:job`、skip 审计、v1.0 规则种子、rag 词库客户端，以及只读消费 data-platform `qc-source` / `pending-qc` 与夹具。

## Requirements

### Requirement: 五表及冻结列名

系统 SHALL 持久化 `qc_records`、`qc_violations`、`qc_alerts`、`qc_reviews`、`qc_rules`，对齐 step4/01 DDL。`qc_records.session_id` MUST 为 `UNIQUE REFERENCES call_records(session_id)`，且 MUST NOT 删除。MUST NOT 存在 `quality_id` 列。对外主键名 SHALL 为 `qc_record_id` = `qc_records.id`。

库列 SHALL 保持 `compliance_score`、`risk_level`、`qc_result`、`grade`，并新增 `deduction_total`。系统 MUST NOT 把这些列改名为 `score`、`risk` 或 `deduction`。HTTP 与事件 JSON 键仅为序列化层：HTTP 使用 `deduction_total` / `compliance_score` / `risk` / `qc_result` / `qc_status` / `grade`（恒 null）；`qc.check.completed` 使用 `deduction` / `score` / `risk` / `grade`（恒 null）。HTTP MUST NOT 用 `score` 或 `deduction` 作为字段名。事件 JSON MUST NOT 使用 `risk_level`、`compliance_score` 或 `deduction_total` 作为键名。

`qc_result` MUST 为 `compliant|violation|pending_review` 之一。`qc_status` MUST 为 `DONE|PENDING_REVIEW|REVIEWED|PARTIAL` 之一。`qc_status` MUST NOT 存储 `WAIT_TEXT` 或 `FAILED`。`grade` 有值时 MUST 为 `EXCELLENT|PASS|WARNING|SERIOUS` 之一；本变更后 `grade` SHALL 恒为 NULL。`qc_alerts.level` MUST 仅为 `high`。表 MUST NOT 增加 `skipped_rules` 列。

#### Scenario: 保留 session_id 唯一外键

- **WHEN** Alembic 创建质检表
- **THEN** `qc_records.session_id` 为 UNIQUE 且 REFERENCES `call_records(session_id)`

#### Scenario: 列名不是 HTTP 别名

- **WHEN** 检查 `qc_records` DDL/ORM
- **THEN** 列为 `deduction_total`、`compliance_score` 与 `risk_level`，不是 `deduction`、`score` 或 `risk`

#### Scenario: HTTP 不把 score 当库列

- **WHEN** 从行序列化 records HTTP body
- **THEN** JSON 使用 `deduction_total`、`compliance_score` 与 `risk`；ORM/表仍为 `deduction_total`、`compliance_score` 与 `risk_level`

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

### Requirement: skipped_rules 耐久且不加列

系统 MUST NOT 给 `qc_records` 加 `skipped_rules` 列，MUST NOT 为此改 CHECK 枚举。任务终态（DONE / PENDING_REVIEW / PARTIAL）时系统 SHALL 写 `audit_logs`：`action=qc.skip.recorded`，`resource_type=qc_records`，`resource_id=qc_record_id`，`detail.skipped_rules`（空数组也写）。GET 详情回源顺序 SHALL 为 Redis `qc:job` → `audit_logs` → `[]`。

#### Scenario: 空 skipped_rules 仍审计

- **WHEN** 任务结束且无 skip 规则
- **THEN** `audit_logs` 仍有 `qc.skip.recorded` 且 `skipped_rules=[]`

#### Scenario: Redis 删除后仍返回 skipped_rules

- **WHEN** DEL `qc:job:{session_id}` 后再 GET 详情
- **THEN** `skipped_rules` 从 `audit_logs` 回源

### Requirement: 规则种子 v1.0 含 R14 十八词

系统 SHALL 种子 `qc_rules.version=v1.0` 为默认 active 包：penalty high=20 / medium=8 / low=2，`same_word_window_ms=30000`。R14 `params.keywords` MUST 恰好为这 18 个字符串：`起诉`、`立案`、`强制执行`、`拘留`、`坐牢`、`判刑`、`刑事责任`、`逮捕`、`征信花了`、`征信洗白`、`上黑名单`、`失信被执行`、`限制高消费`、`追究法律责任`、`冻结银行卡`、`影响子女`、`断供坐牢`、`无法贷款`。种子 MUST NOT 含 `我是法院的`。全库 MUST 仅一版 active。

#### Scenario: 默认 R14 关键词为十八词

- **WHEN** v1.0 为 active
- **THEN** R14 关键词等于该十八词列表，生产环境 R14 不会因空词而 skip

### Requirement: rag 客户端只拉 current.dict

系统 SHALL GET `{QC_RAG_SENSITIVE_BASE}/api/v1/sensitive-words/current.dict`，`text/plain`。超时 SHALL 为 3s，重试 3×，连续 2 次失败熔断 SHALL 保持上一版 AC 自动机。当 `QC_R14_RAG_ENABLED=true` 时，同一 rag 基址 MUST 允许 `POST /api/v1/kb/search`（见 `qc-service/kb-search`）；为 false 时 MUST NOT 调用知识库检索 HTTP。演示默认 `QC_R14_RAG_ENABLED` MUST 为 true。演示默认词库 SHALL 为 rag 约 50 词 `current.dict`，MUST NOT 以 7 词 mock 为默认。

#### Scenario: 词库拉取失败保持旧 AC

- **WHEN** current.dict 连续超时两次
- **THEN** 继续使用上一版 AC 自动机

#### Scenario: 开关关闭时不检索

- **WHEN** `QC_R14_RAG_ENABLED=false`
- **THEN** 不发起 `/kb/search`

### Requirement: DP qc-source 与 pending-qc 只读客户端

qc-service SHALL 仅作为 HTTP 客户端消费 data-platform：

- `GET {QC_DP_BASE}/api/v1/data/sessions/{session_id}/qc-source`（超时 5s，重试 3×）
- `GET {QC_DP_BASE}/api/v1/data/sessions:pending-qc?etl_date=&limit=`（超时 5s，重试 3×）

字段映射 MUST 逐行遵循 DD-02 §4.6.1。`result` MUST 出键（允许 JSON null）。缺失 `dial_history_24h` SHALL 整段省略（R05 skip）。M1 下 `ptp_amount` 与 `standard_answer_hit` SHALL 为 JSON null。qc MUST NOT 反查 `cases`。data-platform MUST NOT HTTP 回调 qc。pending-qc SHALL 只返回 `session_ids[]`；每个 id SHALL 再调 qc-source。pending-qc 熔断 SHALL 使用进程内 `CircuitBreaker`，key `qc:mcp_degraded:data_platform`，open 时跳过本轮补检。qc-source 与 pending-qc 的 HTTP 有界重试 SHALL 使用共享 `call_with_retry`。

本 change MUST NOT 实现 data-platform ETL 或宽表。本 change MUST NOT 实现 call-service。qc-service MUST NOT HTTP 调用 call-service。

#### Scenario: qc-source 映射夹具

- **WHEN** 加载 qc-source 夹具
- **THEN** 含 `result`（可空）；聚合失败时省略 `dial_history_24h`；`ptp_amount`/`standard_answer_hit` 为 null

#### Scenario: pending-qc 熔断跳过补检

- **WHEN** pending-qc 连续失败达阈值使 `qc:mcp_degraded:data_platform` 熔断 open
- **THEN** 本轮补检跳过，不再拉取 qc-source

#### Scenario: 本 change 无 DP ETL

- **WHEN** 实现本 change
- **THEN** qc-service 下未增加 data-platform ETL/宽表代码

### Requirement: 扣分与制度依据列

`qc_records` SHALL 新增 `deduction_total INT NOT NULL DEFAULT 0 CHECK (deduction_total >= 0)`，无 100 上限。`qc_violations` SHALL 新增 `points INT NOT NULL DEFAULT 0 CHECK (points >= 0)`、`policy_clause VARCHAR(100)`、`policy_text TEXT`。`qc_reviews` SHALL 新增 `revised_deduction INT CHECK (revised_deduction >= 0)`。`qc_rules` SHALL 新增 `deduction_policy JSONB`。上述变更 SHALL 由 Alembic 迁移 `0016_qc_deduction_model` 落地，且 down 迁移 MUST 可回滚。

迁移 SHALL 回填：现有行 `deduction_total = 100 - compliance_score`；score_eligible 违规行 `points = penalty[severity]`；`revised_deduction = 100 - revised_score`。`compliance_score=0` 的旧行实际扣分已被截断不可恢复，`deduction_total` SHALL 回填 100 并视为已知近似（仅限历史数据）。PARTIAL 行 `deduction_total` 落 0 仅占位。

`compliance_score` 列 MUST 保留且继续写入派生值 `max(0, 100 - deduction_total)`；`grade` 列 MUST 保留但恒为 NULL；`revised_score` 列 MUST 保留。MUST NOT DROP 或改名上述任一列。

#### Scenario: 迁移含新列与 CHECK

- **WHEN** 执行迁移 0016 upgrade
- **THEN** 四表新列存在且 CHECK `>=0` 生效；downgrade 后新列移除

#### Scenario: 历史行近似回填

- **WHEN** 迁移回填一条 `compliance_score=0` 的历史行
- **THEN** `deduction_total=100`（截断近似），且一致性断言 `compliance_score == max(0, 100 - deduction_total)` 成立

### Requirement: 规则种子含 deduction_policy

active 规则包 SHALL 含 `deduction_policy`：`rule_id → {clause_no, clause_text}` 的静态制度条款映射，并含 `__ac_default__` 兜底节点（按 `redline_tag` 优先、`severity` 次之映射）。`deduction_policy` MUST NOT 携带 points 字段（points 由 severity→penalty 推出）。种子 MUST NOT 含 grade 阈值（`grade_thresholds` 移除）。penalty 与 `same_word_window_ms=30000` 保持 v1.0 数值不变。`deduction_policy`  SHALL 随规则包版本化，经 `POST /api/v1/qc/rules/apply` 原子切换。

#### Scenario: active 包含制度条款

- **WHEN** 读取 active 规则包
- **THEN** 存在 `deduction_policy` 且 R01–R15 中需计分规则均有 `{clause_no, clause_text}`，含 `__ac_default__`；不存在 `grade_thresholds`

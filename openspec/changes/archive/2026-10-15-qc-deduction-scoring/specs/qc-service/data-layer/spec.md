## MODIFIED Requirements

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

### Requirement: 规则种子 v1.0 含 R14 十八词

系统 SHALL 种子 `qc_rules.version=v1.0` 为默认 active 包：penalty high=20 / medium=8 / low=2，`same_word_window_ms=30000`。R14 `params.keywords` MUST 恰好为这 18 个字符串：`起诉`、`立案`、`强制执行`、`拘留`、`坐牢`、`判刑`、`刑事责任`、`逮捕`、`征信花了`、`征信洗白`、`上黑名单`、`失信被执行`、`限制高消费`、`追究法律责任`、`冻结银行卡`、`影响子女`、`断供坐牢`、`无法贷款`。种子 MUST NOT 含 `我是法院的`。全库 MUST 仅一版 active。

#### Scenario: 默认 R14 关键词为十八词

- **WHEN** v1.0 为 active
- **THEN** R14 关键词等于该十八词列表，生产环境 R14 不会因空词而 skip

## ADDED Requirements

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

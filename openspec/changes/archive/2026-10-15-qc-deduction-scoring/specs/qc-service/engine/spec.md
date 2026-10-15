## MODIFIED Requirements

### Requirement: DedupACWindow 只作用于 AC

窗口 `W` SHALL 为 `same_word_window_ms`，默认 30000。同一 speaker、同一 `word_id`（否则归一化词）的配对 SHALL 按最高可用精度且不混用：hit_ts 差 < W，否则 start_offset_ms 差 < W，否则 `|turn_index|≤2`。窗口 MUST NOT 作用于 R01–R15 规则 hit。

#### Scenario: 同词 20s 内计一次

- **WHEN** 同一 AC 词在 20 秒内命中两次且有 hit_ts
- **THEN** 第二条 AC hit 丢弃，扣分合计按一次低危计 2（仅该 hit 时 `deduction_total=2`）

#### Scenario: 回退用 turn_index N=2

- **WHEN** 两条 AC hit 无 hit_ts 与 start_offset_ms，且 `|turn_index1-turn_index2|=1`
- **THEN** 后一条被丢弃

### Requirement: MergeHits 去重键与严重度

MergeHits SHALL 丢弃 `{agent,user}` 以外的 speaker。用户 hit SHALL 设 `score_eligible=false`。LLM hit 的 `rule_id` 不在 R01–R15 时 SHALL 丢弃。去重键 SHALL 为 AC `(speaker, word_id|normalized_word, turn_id)` 与规则/LLM `(speaker, rule_id, turn_id)`。同一 `(speaker,turn_id)` 且非空 `redline_tag` 相同 SHALL 合为一冲突组。保留最高 severity；并列优先 AC > 规则 > LLM。组内若有 rule_id，合并 hit SHALL 保留该 `rule_id`。告警与 suspected/stop_call 事件 MUST 只用合并后 hits。MergeHits 后，高危告警 SHALL 仅当 `speaker=agent` 且 `severity=high`。用户 high、R05（low）、未合并重复 hit MUST NOT 写 `qc_alerts` 或发布 `qc.alert.raised`。

#### Scenario: 同轮 AC threat 与 R11 只扣一次

- **WHEN** 同一轮同时有 AC threat 与 R11
- **THEN** 保留一条 high hit，扣分合计为 20 而非 28，且产生一条高危告警

## REMOVED Requirements

### Requirement: 打分、grade 与 risk_level 独立

**Reason**: 100 分制退役，主口径改为扣分合计 `deduction_total`；grade 分档整体退役；DD-05 明确绩效由外部 HR 系统处理，本系统不消费合规分。

**Migration**: 记录主口径改读 `deduction_total`；`compliance_score` 降级为派生兼容列（灰度期继续写 `max(0, 100 - deduction_total)`）；`grade` 恒为 NULL；判定一律以 `deduction_total` 为准。DD-02 同步升版 V1.18。

## ADDED Requirements

### Requirement: 扣分合计与 risk_level 口径

打分 SHALL 仅对 `score_eligible` hit 累计扣分：high 20 / medium 8 / low 2，从 active 规则包 `qc_rules.penalty` 读取（severity→分值为唯一事实源，MUST NOT 使用硬编码分值常量）。`deduction_total` SHALL 为各 score_eligible hit 扣分之和，无上限。库落 `deduction_total` 与派生 `compliance_score = max(0, 100 - deduction_total)`；一致性断言 `compliance_score == max(0, 100 - deduction_total)` MUST 恒成立。HTTP body 同一扣分值 SHALL 为 `deduction_total`；`qc.check.completed` 同一扣分值 SHALL 为 `deduction`。库落 `risk_level`；HTTP 与事件 JSON SHALL 用 `risk`。系统 MUST NOT 把库列改名为 `risk`/`score`/`deduction`。

`grade` MUST NOT 再被计算；库列 `grade` SHALL 恒为 NULL；HTTP 与事件 JSON 中 `grade` SHALL 为 null。`GRADE_VALUES` 枚举仅保留供 CHECK。

`risk_level` SHALL 为：合并 hits 中 agent+high → `high`；否则累计合格扣分 ≥20 → `medium`；否则 `low`。`risk_level` MUST NOT 用 `compliance_score` 或 `deduction_total` 分档切。用户行与 R05（low）MUST NOT 把 `risk_level` 置为 `high`。

未复核时 `qc_result` / `qc_status` 映射（本期冻结，沿用现网）：可计分扣分合计 >0 SHALL 设 `qc_result=pending_review` 且 `qc_status=PENDING_REVIEW`；=0 SHALL 设 `qc_result=compliant` 且 `qc_status=DONE`。分数/扣分 MUST NOT 越过该映射改写 `qc_result`。RUNNING 中崩溃 SHALL 落 `PARTIAL` 且 A2001 HTTP 200；PARTIAL 行 `deduction_total` 落 0 仅占位，语义以 `qc_status=PARTIAL` 为准，MUST NOT 解读为「无扣分」。raw replay 无 `call_records` SHALL 返回 A2003 且 MUST NOT 插 `qc_records`。

`qc.complaint.suspected` 资格 SHALL 由合并 hits 计算：仅 `speaker=agent` 且 `severity=high` 且 `rule_id∈{R11,R12,R13}`。R05 即使 `redline_tag=threat` MUST NOT 合格。`qc.stop_call.detected` SHALL 在合并 hits 含坐席 R13 且为停呼场景时合格。仅用户原话 MUST NOT 发布 stop_call 或 suspected。

#### Scenario: 单条 agent high 的落库口径

- **WHEN** 合并后恰好一条 agent high（扣 20）
- **THEN** 库存 `deduction_total=20`、`compliance_score=80`、`risk_level=high`、`grade=NULL`、`qc_result=pending_review`

#### Scenario: 仅坐席中低危进人工队列

- **WHEN** 合并 hits 含坐席 medium/low 且无 agent high，且可计分扣分 >0
- **THEN** 库存 `qc_result=pending_review` 且 `qc_status=PENDING_REVIEW`；`risk_level` 为 `low` 或 `medium`（扣分≥20），无 agent-high 时不得为 `high`

#### Scenario: 零扣分即合规完结

- **WHEN** 合并 hits 无可计分扣分（`deduction_total=0`）
- **THEN** `qc_result=compliant`、`qc_status=DONE`，不进人工复核队列

#### Scenario: 一致性断言恒成立

- **WHEN** 任意 `deduction_total`（含 >100 的多 high 累加）
- **THEN** 落库 `compliance_score == max(0, 100 - deduction_total)` 恒成立

#### Scenario: R05 threat 标签不发 suspected

- **WHEN** 仅有 R05 hits（agent、low、tag=threat）
- **THEN** 扣 2 分（`deduction_total=2`），`qc_status=PENDING_REVIEW`，无 `qc_alerts` 行，且 suspected 不合格

### Requirement: 每条扣分携带制度依据

每条落库违规 SHALL 携带本条扣分 `points`（由 severity→active 规则包 penalty 确定性推出）、制度条款号 `policy_clause` 与条款原文 `policy_text`，来源为 active 规则包 `qc_rules.deduction_policy`（`rule_id → {clause_no, clause_text}`）。`policy_text` MUST 为静态制度条款原文，MUST NOT 与 RAG 检索片段 `kb_snippet` 混用同一字段。无 `rule_id` 的纯 AC hit SHALL 由 `deduction_policy.__ac_default__` 兜底节点映射，按 `redline_tag` 优先、`severity` 次之。RAG 仅在 `QC_R14_RAG_ENABLED=true` 时做会话级补 `kb_snippet` 原文；检索失败 SHALL 回退静态条款，MUST NOT 置 PARTIAL。

#### Scenario: 规则命中带出条款

- **WHEN** R12 命中且 active 包 `deduction_policy` 含 R12 条款
- **THEN** 该违规行 `points=20`、`policy_clause` 与 `policy_text` 为 R12 对应制度条款

#### Scenario: 纯 AC hit 走兜底条款

- **WHEN** 合并 hit 为纯 AC threat 且无 `rule_id`
- **THEN** `policy_clause`/`policy_text` 由 `__ac_default__` 按 `redline_tag=threat` 映射填充

#### Scenario: RAG 失败回退静态条款

- **WHEN** `QC_R14_RAG_ENABLED=true` 且 `/kb/search` 调用失败
- **THEN** 违规行仍以静态 `policy_text` 落库，`kb_snippet` 为空，`qc_status` 不为 PARTIAL

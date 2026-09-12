## MODIFIED Requirements

### Requirement: R01–R15 skip 表

RuleEngine SHALL 按 DD-02 §5.2.2 skip 条件执行 R01–R09、R11–R15。skip MUST NOT 产生 hit，MUST NOT 置 PARTIAL，MUST NOT 称为红线已通过。被 skip 的 `rule_id` SHALL 进入 `skipped_rules[]`。R05 MUST NOT 用单通 turns 猜拨次。R13 用户行 MUST NOT 进 score。R10 MUST NOT 在本轮执行。

R04 SHALL 使用 DD-02 §5.2.4 确认词/披露词；确认后披露 MUST NOT 命中 R04；自称公权力 SHALL 走 R12 而非 R04。R04 MUST NOT 依赖 `debtor_name`。

R14 SHALL 按 DD-02 §5.2.3 比对 `qc_rules` 关键词和/或 `standard_answer_hit`。无关键词且无 hit_text 且检索未命中 SHALL skip R14。当 `QC_R14_RAG_ENABLED=true` 时 R14 MUST 允许调用 `POST /kb/search` 并将命中写入 `standard_answer_hit` 或 hits 条款字段；为 false 时 MUST NOT 检索。R14 severity SHALL 为 medium，默认 `QC_ALERT_LEVEL_MIN=high` 时 MUST NOT 告警。演示默认 `QC_R14_RAG_ENABLED` MUST 为 true。

缺护栏日志 MUST NOT 标 `unverified`，MUST NOT 伪造 R10 hit。缺 `result` 时 R10 skip SHALL 只在合并后 R10 步骤求值，不在本轮规则中求值。

#### Scenario: 缺 debtor_name 则 skip R01

- **WHEN** QCSession 无 `debtor_name`
- **THEN** skip R01，无 R01 hit，且 `skipped_rules` 含 `R01`

#### Scenario: R14 永不检索知识库

- **WHEN** `QC_R14_RAG_ENABLED=false` 且求值 R14
- **THEN** 无论命中或 skip，均不 HTTP 调用 `/kb/search`

#### Scenario: R14 开关开启时检索知识库

- **WHEN** `QC_R14_RAG_ENABLED=true` 且求值 R14
- **THEN** 允许 HTTP 调用 `/kb/search`；失败则回退关键词，不 PARTIAL

### Requirement: 打分、grade 与 risk_level 独立

打分 SHALL 从 100 起，仅对 `score_eligible` hit 扣分：high−20 / medium−8 / low−2，下限 0。库落 `compliance_score`。HTTP body 同一值 SHALL 为 `compliance_score`。`qc.check.completed` 同一值 SHALL 为 `score`。库落 `risk_level`；HTTP 与事件 JSON SHALL 用 `risk`。系统 MUST NOT 把库列改名为 `risk`/`score`。

`risk_level` SHALL 为：合并 hits 中 agent+high → `high`；否则累计合格扣分 ≥20 → `medium`；否则 `low`。`risk_level` MUST NOT 用 `compliance_score` 分档切。用户行与 R05（low）MUST NOT 把 `risk_level` 置为 `high`。

`grade` SHALL 按分数阈值：≥90 EXCELLENT / ≥80 PASS / ≥60 WARNING / <60 SERIOUS。`grade` 与 `risk_level` SHALL 独立。单条 agent high（−20）SHALL 同时得到 `compliance_score=80`、`risk_level=high`、`grade=PASS`。

未复核时 `qc_result` / `qc_status` 映射：可计分扣分合计 >0 SHALL 设 `qc_result=pending_review` 且 `qc_status=PENDING_REVIEW`（含仅中低危扣分；不再因「无 agent-high」自动 `DONE`）。可计分扣分合计 =0 SHALL 设 `qc_result=compliant` 且 `qc_status=DONE`。分数 MUST NOT 改写 `qc_result`。RUNNING 中崩溃 SHALL 落 `PARTIAL` 且 A2001 HTTP 200。raw replay 无 `call_records` SHALL 返回 A2003 且 MUST NOT 插 `qc_records`。

`qc.complaint.suspected` 资格 SHALL 由合并 hits 计算：仅 `speaker=agent` 且 `severity=high` 且 `rule_id∈{R11,R12,R13}`。R05 即使 `redline_tag=threat` MUST NOT 合格。`qc.stop_call.detected` SHALL 在合并 hits 含坐席 R13 且为停呼场景时合格（用户说停止致电/不要再打后坐席仍催收）。仅用户原话 MUST NOT 发布 stop_call 或 suspected。

#### Scenario: 80 分且 high 风险且 PASS 档

- **WHEN** 合并后恰好一条 agent high（−20）
- **THEN** 库存 `compliance_score=80`、`risk_level=high`、`grade=PASS`、`qc_result=pending_review`

#### Scenario: 仅坐席中低危为 violation DONE

- **WHEN** 合并 hits 含坐席 medium/low 且无 agent high，且可计分扣分 >0
- **THEN** 库存 `qc_result=pending_review` 且 `qc_status=PENDING_REVIEW`；`risk_level` 为 `low` 或 `medium`（扣分≥20），无 agent-high 时不得为 `high`

#### Scenario: R05 threat 标签不发 suspected

- **WHEN** 仅有 R05 hits（agent、low、tag=threat）
- **THEN** 扣 2 分，`qc_status=PENDING_REVIEW`，无 `qc_alerts` 行，且 suspected 不合格

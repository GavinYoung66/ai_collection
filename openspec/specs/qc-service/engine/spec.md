## Purpose

定义 qc-service M1 离线检测链：AC 匹配、30s AC 窗口、R01–R15 skip、MergeHits、合并后 R10、打分，使 `compliance_score`、`risk_level`、`grade` 可复现。

## Requirements

### Requirement: 检测顺序冻结

离线质检 SHALL 按此顺序执行，MUST NOT 乱序写入 hits：(1) 按轮 AC (2) 仅对 AC 做 DedupACWindow (3) 规则 R01–R09 与 R11–R15（含 skip）(4) LLM 复核或熔断时为空 (5) MergeHits (6) 用合并后 hits 做 R10 (7) 打分 (8) 告警/事件。R10 MUST NOT 在第一轮规则中执行。

#### Scenario: R10 在合并之后

- **WHEN** 会话有合并后的 agent threat hits 且 `result=completed`
- **THEN** R10 在 MergeHits 之后求值，并可追加一条会话级 R10 hit

### Requirement: AC 扣分仅坐席

AC SHALL 复用 `common.security.sensitive_words` 与护栏三分类映射（threat=high，abuse=medium，induce=low）。仅 `speaker=agent` 的 hit SHALL 计入扣分。用户提及投诉/自杀/停呼 MUST NOT 扣分。入站 `speaker=ai` MUST 映射为 `agent`。AC 适配器 MUST NOT 读取 call-service 护栏放行日志。

#### Scenario: 用户自杀提及不扣分

- **WHEN** 用户表达自杀意图且坐席安抚、未继续催收施压
- **THEN** 用户行不计分，且不得仅凭用户原话记坐席违规

### Requirement: DedupACWindow 只作用于 AC

窗口 `W` SHALL 为 `same_word_window_ms`，默认 30000。同一 speaker、同一 `word_id`（否则归一化词）的配对 SHALL 按最高可用精度且不混用：hit_ts 差 < W，否则 start_offset_ms 差 < W，否则 `|turn_index|≤2`。窗口 MUST NOT 作用于 R01–R15 规则 hit。

#### Scenario: 同词 20s 内计一次

- **WHEN** 同一 AC 词在 20 秒内命中两次且有 hit_ts
- **THEN** 第二条 AC hit 丢弃，分数按一次低危扣分（仅该 hit 时 100-2=98）

#### Scenario: 回退用 turn_index N=2

- **WHEN** 两条 AC hit 无 hit_ts 与 start_offset_ms，且 `|turn_index1-turn_index2|=1`
- **THEN** 后一条被丢弃

### Requirement: R01–R15 skip 表

RuleEngine SHALL 按 DD-02 §5.2.2 skip 条件执行 R01–R09、R11–R15。skip MUST NOT 产生 hit，MUST NOT 置 PARTIAL，MUST NOT 称为红线已通过。被 skip 的 `rule_id` SHALL 进入 `skipped_rules[]`。R05 MUST NOT 用单通 turns 猜拨次。R13 用户行 MUST NOT 进 score。R10 MUST NOT 在本轮执行。

R04 SHALL 使用 DD-02 §5.2.4 确认词/披露词；确认后披露 MUST NOT 命中 R04；自称公权力 SHALL 走 R12 而非 R04。R04 MUST NOT 依赖 `debtor_name`。

R14 SHALL 按 DD-02 §5.2.3 比对 `qc_rules` 关键词和/或 `standard_answer_hit`。无关键词且无 hit_text SHALL skip R14。R14 MUST NOT 调用 `POST /kb/search`。R14 severity SHALL 为 medium，默认 `QC_ALERT_LEVEL_MIN=high` 时 MUST NOT 告警。`QC_R14_RAG_ENABLED` MUST 为 false。

缺护栏日志 MUST NOT 标 `unverified`，MUST NOT 伪造 R10 hit。缺 `result` 时 R10 skip SHALL 只在合并后 R10 步骤求值，不在本轮规则中求值。

#### Scenario: 缺 debtor_name 则 skip R01

- **WHEN** QCSession 无 `debtor_name`
- **THEN** skip R01，无 R01 hit，且 `skipped_rules` 含 `R01`

#### Scenario: R14 永不检索知识库

- **WHEN** M1 求值 R14
- **THEN** 无论命中或 skip，均不 HTTP 调用 `/kb/search`

### Requirement: MergeHits 去重键与严重度

MergeHits SHALL 丢弃 `{agent,user}` 以外的 speaker。用户 hit SHALL 设 `score_eligible=false`。LLM hit 的 `rule_id` 不在 R01–R15 时 SHALL 丢弃。去重键 SHALL 为 AC `(speaker, word_id|normalized_word, turn_id)` 与规则/LLM `(speaker, rule_id, turn_id)`。同一 `(speaker,turn_id)` 且非空 `redline_tag` 相同 SHALL 合为一冲突组。保留最高 severity；并列优先 AC > 规则 > LLM。组内若有 rule_id，合并 hit SHALL 保留该 `rule_id`。告警与 suspected/stop_call 事件 MUST 只用合并后 hits。MergeHits 后，高危告警 SHALL 仅当 `speaker=agent` 且 `severity=high`。用户 high、R05（low）、未合并重复 hit MUST NOT 写 `qc_alerts` 或发布 `qc.alert.raised`。

#### Scenario: 同轮 AC threat 与 R11 只扣一次

- **WHEN** 同一轮同时有 AC threat 与 R11
- **THEN** 保留一条 high hit，分数扣 20 而非 28，且产生一条高危告警

### Requirement: R10 合并后算法

R10 SHALL 至多追加一条会话级 hit（`rule_id=R10`，`redline_tag=unverified`，`speaker=agent`，`severity=high`），且仅当全部成立：`result` 存在、`result≠escalated`，且合并后 agent hits 满足 `redline_tag∈{threat,fake_identity}` 至少 1 次，**或** `redline_tag∈{abuse,induce}` 累计 ≥2。`result==escalated` SHALL 为非命中（不是 skip）。缺 `result` SHALL skip R10（`skipped_rules` 含 `R10`），MUST NOT 扣分，MUST NOT 告警，MUST NOT 用 `unverified` 填补缺失护栏日志。

#### Scenario: completed 且 agent threat 命中 R10

- **WHEN** `result=completed` 且合并 hits 含 agent threat
- **THEN** 追加一条 R10 hit

#### Scenario: 缺 result 则 skip R10 且不填 unverified

- **WHEN** `QCSession.result` 缺失
- **THEN** skip R10，`skipped_rules` 含 `R10`，且不写 `unverified` 填缺口

### Requirement: 打分、grade 与 risk_level 独立

打分 SHALL 从 100 起，仅对 `score_eligible` hit 扣分：high−20 / medium−8 / low−2，下限 0。库落 `compliance_score`。HTTP body 同一值 SHALL 为 `compliance_score`。`qc.check.completed` 同一值 SHALL 为 `score`。库落 `risk_level`；HTTP 与事件 JSON SHALL 用 `risk`。系统 MUST NOT 把库列改名为 `risk`/`score`。

`risk_level` SHALL 为：合并 hits 中 agent+high → `high`；否则累计合格扣分 ≥20 → `medium`；否则 `low`。`risk_level` MUST NOT 用 `compliance_score` 分档切。用户行与 R05（low）MUST NOT 把 `risk_level` 置为 `high`。

`grade` SHALL 按分数阈值：≥90 EXCELLENT / ≥80 PASS / ≥60 WARNING / <60 SERIOUS。`grade` 与 `risk_level` SHALL 独立。单条 agent high（−20）SHALL 同时得到 `compliance_score=80`、`risk_level=high`、`grade=PASS`。

未复核时 `qc_result` / `qc_status` 映射：agent+high SHALL 设 `qc_result=pending_review` 且 `qc_status=PENDING_REVIEW`；有坐席违规但无 high SHALL 设 `qc_result=violation` 且 `qc_status=DONE`；无坐席违规 SHALL 设 `qc_result=compliant` 且 `qc_status=DONE`。分数 MUST NOT 改写 `qc_result`。RUNNING 中崩溃 SHALL 落 `PARTIAL` 且 A2001 HTTP 200。raw replay 无 `call_records` SHALL 返回 A2003 且 MUST NOT 插 `qc_records`。

`qc.complaint.suspected` 资格 SHALL 由合并 hits 计算：仅 `speaker=agent` 且 `severity=high` 且 `rule_id∈{R11,R12,R13}`。R05 即使 `redline_tag=threat` MUST NOT 合格。`qc.stop_call.detected` SHALL 在合并 hits 含坐席 R13 且为停呼场景时合格（用户说停止致电/不要再打后坐席仍催收）。仅用户原话 MUST NOT 发布 stop_call 或 suspected。

#### Scenario: 80 分且 high 风险且 PASS 档

- **WHEN** 合并后恰好一条 agent high（−20）
- **THEN** 库存 `compliance_score=80`、`risk_level=high`、`grade=PASS`、`qc_result=pending_review`

#### Scenario: 仅坐席中低危为 violation DONE

- **WHEN** 合并 hits 含坐席 medium/low 且无 agent high
- **THEN** 库存 `qc_result=violation` 且 `qc_status=DONE`；`risk_level` 为 `low` 或 `medium`（扣分≥20），无 agent-high 时不得为 `high`

#### Scenario: R05 threat 标签不发 suspected

- **WHEN** 仅有 R05 hits（agent、low、tag=threat）
- **THEN** 扣 2 分，无 `qc_alerts` 行，且 suspected 不合格

## MODIFIED Requirements

### Requirement: 断言 1–28 可执行

系统 SHALL 将 DD-02 §8.2 断言 1–28 做成可执行用例（V1.18 扣分口径）。QC-11.2 仍是一个任务 ID：轮次 3.2 SHALL 覆盖断言 1–14；轮次 3.3 SHALL 覆盖断言 15–28。断言 5 SHALL 订阅 `qc.escalate.human` 且无消息。断言 23/24 SHALL 断言零次 rag `/kb/search` 调用。断言 26 MAY 使用 Mock DP。断言 28 SHALL DEL Redis 后从 audit_logs GET 回 `skipped_rules`。

| # | SHALL |
| :--- | :--- |
| 1 | raw replay 含坐席高危话术且 `call_ended=true` → `deduction_total≥40`，risk=high，grade=null。夹具 SHALL 含足够多可计分的 agent-high hits 使 `deduction_total≥40`（MUST NOT 用单条 20） |
| 2 | `qc_records` high_count=1 |
| 3 | GET `/qc/alerts` 返回 1 条 NEW 高危告警 |
| 4 | PSUBSCRIBE `qc.alert.raised` 收到该告警 |
| 5 | 订阅 `qc.escalate.human` → 无消息 |
| 6 | 同词 20s 内两次 → 30s 去重，`deduction_total=2` |
| 7 | 23:30 通话 → R02 命中，扣 8 |
| 8 | 用户停呼后坐席继续 → R13 + `qc.stop_call.detected`；用户行不计分 |
| 9 | 「我是法院的」→ R12 fake_identity high |
| 10 | POST reviews CORRECT → `deduction_total=revised_deduction`，`qc_status=REVIEWED`，risk_level 不变 |
| 11 | POST replay `{session_id}` 从 DP 种子成功 |
| 12 | LLM mock 熔断 → 仍出 AC+规则扣分结果 |
| 13 | evidence_text 无完整手机号/身份证 |
| 14 | `call_ended=false` 或空 turns → A2002，无 DONE 行 |
| 15 | 用户自杀 + 坐席安抚、未再催收 → 不扣坐席分，不发 suspected |
| 16 | 用户提及投诉后坐席仍催收 → R13，扣坐席分 |
| 17 | 未结束会话 replay → A2003，Redis WAIT_TEXT，无 `qc_records` |
| 18 | raw replay 无 `call_records` → A2003，不插表 |
| 19 | 同轮 AC threat + R11 → 只扣 20；1 条高危告警 |
| 20 | 无时间戳、turn_index 差 1 → 第二条 AC 丢弃 |
| 21 | 用户自杀 + 坐席安抚（无 R13）→ 无 qc_alerts，不发 suspected |
| 22 | 仅 R05 → 扣 2，无告警，不发 suspected |
| 23 | R14 无关键词且无 hit_text → skipped_rules 含 R14；无 `/kb/search`；仅因 skip R14 MUST NOT 把 `qc_status` 置 PARTIAL，允许为 DONE |
| 24 | R14 关键词或 hit_text 命中 → R14 hit；仍无 RAG 检索 HTTP |
| 25 | `result=completed` + agent threat → R10 命中；无 result → skip R10，不填 unverified |
| 26 | qc-source 有 `result` 键（可空）；缺 dial_history → skip R05；`ptp_amount` null → skip R08 |
| 27 | R04：身份确认前披露命中；「是我」之后不命中 |
| 28 | 写入 skipped_rules=["R14"]，DEL Redis job，GET 仍返回该数组 |

#### Scenario: 轮次 3.2 覆盖 1–14

- **WHEN** 执行验收轮次 3.2
- **THEN** 断言 1–14（含无 `qc.escalate.human` 消息）可执行且通过

#### Scenario: 断言 1 夹具不是单条 −20

- **WHEN** 跑断言 1 raw replay
- **THEN** `deduction_total≥40`、`risk=high`、`grade` 为 null（夹具有足够 agent-high；MUST NOT 用单条扣 20）

#### Scenario: 轮次 3.3 覆盖 15–28

- **WHEN** 执行验收轮次 3.3
- **THEN** 断言 15–28（含 23/24 无 `/kb/search`）可执行且通过

## ADDED Requirements

### Requirement: 扣分口径一致性断言

验收 SHALL 覆盖一致性断言：任意记录落库后 `compliance_score == max(0, 100 - deduction_total)` 恒成立（含 `deduction_total>100` 的多 high 累加场景）。验收 SHALL 覆盖每条违规行 `points` 与 `policy_clause`/`policy_text` 非空（score_eligible 行），且纯 AC 无 `rule_id` 的 hit 由 `__ac_default__` 兜底填充。验收 SHALL 覆盖 DISMISS 后 `deduction_total=0` 且 `qc_result=compliant`。

#### Scenario: 一致性断言含超 100 扣分

- **WHEN** 夹具产生 `deduction_total=120`（多条 agent high 累加）
- **THEN** 落库 `compliance_score=0`，且断言 `compliance_score == max(0, 100 - deduction_total)` 通过

#### Scenario: 违规行带制度依据

- **WHEN** 跑断言 1 后 GET 详情
- **THEN** 每条 score_eligible hit 含 `points`、`policy_clause`、`policy_text`

#### Scenario: DISMISS 归零

- **WHEN** 对 `deduction_total>0` 的记录 POST 复核 DISMISS
- **THEN** `deduction_total=0`、`qc_result=compliant`、`qc_status=REVIEWED`

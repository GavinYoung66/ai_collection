# Proposal: qc-deduction-scoring

## Why

qc-service 现行打分口径为 100 分制（`compliance_score = max(0, 100 - penalty)`），分数是"合规率"，扣分语义被隐藏；`penalty_total` 已算出但未落库；除 R14 外的违规没有制度条款原文，人工复核无从对照；grade 与 risk_level 两套口径并存造成认知负担。DD-05 已明确绩效由外部 HR 系统处理，本系统不消费合规分，100 分制失去存在依据。业务需要的是"只记扣分、每条扣分可审计、人工兜底终态"。

## What Changes

- **打分口径反转**：记录主口径改为 `deduction_total = Σ points`（high 20 / medium 8 / low 2，沿用现值）；`compliance_score` 降级为派生兼容列，灰度期继续写 `max(0, 100 - deduction_total)`。
- **判定权威切换**：`qc_result` / `qc_status` / 复核结论一律以 `deduction_total` 为准；`compliance_score` 仅派生展示。`qc_result` 三值映射本期冻结（=0→compliant/DONE，>0→pending_review/PENDING_REVIEW），不改索引与队列查询。
- **grade 退役**：删除 `grade_for()` 计算与调用；`qc_records.grade` 恒 NULL（列保留、不 DROP）；`GRADE_THRESHOLDS_V1` 移出种子；HTTP 移除 grade 过滤参数；前端移除 grade 列；仅保留 `GRADE_VALUES` 供 CHECK。
- **每条扣分可审计**：`qc_violations` 新增 `points` / `policy_clause` / `policy_text`；`qc_rules` 新增 `deduction_policy JSONB`（rule_id → {clause_no, clause_text}）随规则包版本化；纯 AC 无 rule_id 的 hit 由 `__ac_default__` 兜底节点映射。
- **penalty 单一事实源**：打分与落库统一从 active 规则包 `qc_rules.penalty` 读取，废弃 scoring_service.py 硬编码 `PENALTY` 常量。
- **复核语义修正**：DISMISS → `deduction_total=0` 且 compliant（修掉既存不归零缺陷）；CONFIRM → 扣分不变；CORRECT → `deduction_total=revised_deduction` 且同步派生 `revised_score`；三种动作均不改 `risk_level`（修掉 CORRECT 越权改 risk 缺陷）。
- **事件灰度扩容**：`qc.check.completed` 白名单新增可选键 `deduction`，`score` 保留派生值，`grade` 恒 null；data-platform 本期显式忽略 `deduction`，`ads_case_loop` 列不变。
- **DB 迁移 0016**：`qc_records` +`deduction_total`；`qc_violations` +`points`/`policy_clause`/`policy_text`；`qc_reviews` +`revised_deduction`；`qc_rules` +`deduction_policy`；含近似回填与可回滚 down。
- **DD-02 同步升版 V1.17→V1.18**：§3.1/§4/§5.4（含 5.4.0/5.4.1/5.4.2）/§6.2/§9/§11/changelog/`grade_thresholds` 改写为扣分口径。

**BREAKING（对消费方）**：HTTP records/check/batch/review 响应新增 `deduction_total`/`revised_deduction` 字段，移除 `grade` 查询参数，`grade` 字段恒为 null；前端台账语义由"合规分"改"扣分"。灰度期内 `compliance_score`/`score` 仍下发，消费方无硬切换。

**不做**：不改扣分点数与同词窗口；不新增逐 hit RAG 检索；不删 compliance_score/grade/revised_score 列；不改 `qc_result`/`qc_status` 映射、risk_level、告警、suspected、stop_call 条件；不改 WordHit 数据结构、qc-service 与 call-service 边界、并发参数；DD-02 §5.4「无高危应落 violation」与现网偏差留待后续单独收口。

## Capabilities

### New Capabilities

（无新增 capability）

### Modified Capabilities

- `qc-service/engine`: 打分口径由 100 分制改为扣分合计；penalty 从规则包读取；grade 不再计算；判定以 deduction_total 为准。
- `qc-service/data-layer`: 四表新增列与 CHECK；迁移 0016 含近似回填；qc_rules 增 deduction_policy。
- `qc-service/api-layer`: records/check/batch/review 字段变更；grade 过滤参数移除；revised_deduction 入参。
- `qc-service/events`: qc.check.completed 白名单扩容 deduction 可选键，grade 恒 null。
- `qc-service/acceptance`: 验收口径改扣分制，含一致性断言 compliance_score == max(0,100-deduction_total)。
- `qc-service/demo-ui`: 台账/详情/复核 UI 文案与字段改扣分口径，移除 grade。
- `qc-service/mcp-call-qc`: MCP 违规查询输出补充扣分与制度依据字段（points/policy_clause/policy_text）。

（已评审 `complaint-service/v4-qc-event`：该 capability 约束投诉→质检方向的 `qc.violation.reported`，与本变更的质检→投诉链路无交集；质检→投诉侧仅依赖 `has_high_alert` 与 `qc.complaint.suspected`，二者均不变，故无 delta。）

## Impact

- **代码**：qc-service 引擎（scoring_service/offline_qc_service/observability）、持久化（models/qc、quality_repo、review_repo、rule_repo）、HTTP/事件（records/review/check、publisher、replay_service、review_service、mcp/tools）、种子（qc_rules_v1；demo_scenarios 已核实无 score/grade 字段，不改）、前端 web/app.js；data-platform snapshot/subscriber（仅显式忽略新键）。
- **DB**：迁移 0016（四表加列 + 回填），必须可回滚。
- **API/事件契约**：命名映射 deduction_total(列/HTTP) ↔ deduction(事件键)；score/compliance_score 灰度保留。
- **文档**：DD-02 升版 V1.18。
- **灰度与回滚**：派生列持续写保下游不炸；回滚=切回 compliance_score 主口径 + deduction_total 置 0 + 迁移 down。
- **测试**：qc-service 全套 engine/offline_qc/reviews/records/rules_seed/assertions/merge_hits/r10/events_publish/replay_api/mcp_call_qc/demo_seed 需按新口径修复。

# Design: qc-deduction-scoring

## Context

现网打分链路已算出 `penalty_total` 但未落库；`compliance_score = max(0, 100 - penalty)` 由 [scoring_service.py](../../../services/qc-service/app/services/scoring_service.py) 硬编码 `PENALTY = {high:20, medium:8, low:2}` 驱动（`qc_rules.penalty` 配置列存在但未被读取）；`grade_for()` 分档；`review_service._finalize_reviewed` 重算 grade 且 CORRECT 越权改 `risk_level`；DISMISS 不归零扣分。信封层 `common/eventbus/envelope.py` 的 `DATA_WHITELIST` 仅含 ID 定位字段（case_id/session_id/qc_record_id/complaint_no/backfill_date），score/risk/grade 本就不进信封 `data`。动机见 proposal.md。

## Goals / Non-Goals

**Goals:**
- 主口径反转为 `deduction_total`；每条违规落 `points` + 静态制度条款；grade 退役；penalty 单一事实源迁回规则包；复核语义修正；DD-02 升版 V1.18。
- 灰度可回滚：派生列持续写，下游零硬切。

**Non-Goals:**
- 不改 `qc_result` 三值映射与 `qc_status` 状态机；不动 `idx_qc_records_pending_review` 与队列查询。
- 不改 risk_level 口径、告警门槛、suspected/stop_call 资格。
- 信封 `data` 白名单不改；DP 宽表列不改；complaint-service 不动。
- 扣分点数、同词窗口、RAG 检索频次均不变。

## Decisions

### D1. 新增 `deduction_total` 列，而非复用 `compliance_score` 改语义

选新增列。复用旧列改语义会造成同列两义、回滚无锚点、历史数据无法区分口径。代价是迁移 0016 + 派生列双写，但换来：`compliance_score` 作为回滚锚点与下游灰度桥，一致性断言 `compliance_score == max(0,100-deduction_total)` 可由测试守住。

### D2. `qc_result` 映射本期冻结

DD-02 §5.4 定义「无 agent 高危时应落 violation」，但现网实现为「任意扣分→pending_review」，且人工队列索引 `idx_qc_records_pending_review` 与 `ReviewRepo.list_pending` 均依赖 `qc_result='pending_review'`。改映射会连带索引/查询/前端队列，爆炸半径远超本次目标。本期冻结现网行为（=0→compliant/DONE，>0→pending_review/PENDING_REVIEW），DD-02 偏差在升版 changelog 中显式登记为遗留项，后续单独收口。

### D3. penalty 单一事实源迁回规则包

现网 `qc_rules.penalty` 列存在但打分走硬编码常量，双源漂移。本次打分与落库统一从 active 规则包读取 penalty；`deduction_policy` 不携带 points（由 severity→penalty 确定性推出），避免三源。备选「保留硬编码」被否：与规则版本切换能力（`POST /qc/rules/apply`）矛盾。

### D4. 制度依据静态表 + RAG 补强，字段分离

`qc_rules.deduction_policy JSONB`：`rule_id → {clause_no, clause_text}` + `__ac_default__` 兜底（redline_tag 优先、severity 次之）。落库 `policy_clause`/`policy_text` 由静态表确定性填充；`kb_snippet` 仅存 RAG 检索片段。两字段分离避免「同一列混装条款与检索片段」。RAG 维持会话级单次调用，失败回退静态条款不 PARTIAL。备选「逐 hit RAG」被否：检索量 ×N 且不可复现。

### D5. grade 退役到函数级

删除 `grade_for()` 全部调用点（scoring、review_service._finalize_reviewed）；`GRADE_THRESHOLDS_V1` 移出种子；列与 CHECK 保留不 DROP（回滚需要）；HTTP/事件 `grade` 恒 null；`records.py` 移除 grade 过滤参数。DD-02 中「grade 表达已触发高危人工复核尚未终态」的语义由 `qc_status=PENDING_REVIEW` 承接。

### D6. 复核语义修正

DISMISS → `deduction_total=0` + compliant（修既存不归零缺陷）；CONFIRM → 不变；CORRECT → `deduction_total=revised_deduction`，同步派生写 `revised_score=max(0,100-revised_deduction)` 与 `compliance_score=max(0,100-deduction_total)`。三种动作均不改 `risk_level`（修 CORRECT 越权缺陷）——risk 由 hits 决定，复核不重跑引擎。

### D7. 事件扩容仅在业务构造键层

`qc.check.completed` 业务构造键新增可选 `deduction`（publisher `COMPLETED_KEYS` 扩容），`score` 保留派生值，`grade` 恒 null。**信封白名单结论（已查实）**：`DATA_WHITELIST` 仅含 ID 定位字段，`deduction` 与 score/risk/grade 一样不进信封 `data`，无需放行；DP subscriber 消费信封 `data`，天然不可见 `deduction`，「显式忽略」即不消费。DD-02 §5.4.0「禁止给 completed 增加必填字段」不违反：`deduction` 为可选键。

### D8. 回填为有损近似并显式声明

`deduction_total = 100 - compliance_score` 回填；`compliance_score=0` 的旧行实际扣分被 max(0,·) 截断不可恢复，回填 100 并在迁移注释与本文档显式声明（仅历史数据，新数据无损）。PARTIAL 行 `deduction_total=0` 仅占位。

### D9. DD-02 升版 V1.17→V1.18 清单（章节号已对照 DD-02 实际结构核实）

§3.1（qc_result/qc_status 状态机映射）、§4.0.1.1（库列↔HTTP↔事件键表）、§4.0.3（Hit 公共对象补 points/policy_clause/policy_text）、§4.1.4–4.1.6（records 接口）、§4.3（复核接口）、§4.4（规则版本，含 grade_thresholds 移除与 deduction_policy 校验）、§5.4 正文（100 分制注释、「grade 与 risk 两维独立」段删除改写；含 5.4.0/5.4.1）、§6.1（PostgreSQL 表定义新列）、§8.2（验收断言 1/6/10 改扣分口径）、§9.3（事件 payload 示例）、§0.1（修订记录增 V1.18）、D2 偏差遗留登记。

## Risks / Trade-offs

- [派生列与主列不一致（写路径漏改）] → 一致性断言 `compliance_score == max(0,100-deduction_total)` 进验收；所有写路径收敛于 `_persist` 与 `_finalize_reviewed` 两处。
- [历史数据回填近似（compliance=0 行）] → 显式声明；影响面仅限历史，不回溯修正。
- [消费方误读 `score` 为主口径] → 命名映射表固化（DB `deduction_total` / HTTP `deduction_total` / 事件 `deduction`）；DD-02 §4/§9 同步；灰度期后另起 change 下线 `score`。
- [纯 AC hit 无 rule_id 导致条款缺失] → `__ac_default__` 兜底 + 验收断言每条 score_eligible hit 条款非空。
- [规则包版本切换遗漏 deduction_policy] → `POST /qc/rules/apply` 校验逻辑扩展：缺 deduction_policy 的包判非法版本回退（A2009）。
- [grade 恒 null 破坏旧前端] → 前端同步移除 grade 列；HTTP 保留键位 null 不删键，JSON 结构稳定。

## Migration Plan

1. 迁移 0016 upgrade（仓库根 `migrations/versions/0016_qc_deduction_model.py`，现有最新 0015；加列 + CHECK + 回填），可 down。
2. 种子改造（deduction_policy、移 grade_thresholds），active 包原子切换。
3. 引擎/持久化/HTTP/事件/前端按 tasks.md 分层上线；派生列全程双写。
4. 回滚：代码切回 `compliance_score` 主口径 → `deduction_total` 置 0 → 迁移 down。任一步可独立执行。

## Open Questions

- DP `ads_case_loop` 何时新增 `deduction_total` 列并切主口径：留待下期灰度收口 change，不影响本 change 的 specs 与任务拆分。

# Tasks: qc-deduction-scoring

## 1. 迁移与规则种子

- [x] 1.1 新增 Alembic 迁移 `migrations/versions/0016_qc_deduction_model.py`（**仓库根 migrations/versions/**，现有最新 0015，非 services/qc-service 下）：`qc_records` +`deduction_total INT NOT NULL DEFAULT 0 CHECK (>=0)`；`qc_violations` +`points`/`policy_clause`/`policy_text`；`qc_reviews` +`revised_deduction CHECK (>=0)`；`qc_rules` +`deduction_policy JSONB`；回填 `deduction_total=100-compliance_score`、`points=penalty[severity]`（仅 score_eligible）、`revised_deduction=100-revised_score`；验证：`alembic upgrade head` 后四表新列与 CHECK 存在，`alembic downgrade -1` 可回滚，历史 `compliance_score=0` 行回填为 100
- [x] 1.2 改造 `seeds/qc_rules_v1.py`：加 `deduction_policy`（R01–R15 计分规则的 `{clause_no, clause_text}` + `__ac_default__` 兜底节点），移除 `GRADE_THRESHOLDS_V1`；验证：种子后 active 包含 `deduction_policy` 且无 `grade_thresholds`，`test_rules_seed` 相关断言更新通过

## 2. 引擎层

- [x] 2.1 改造 `scoring_service.py`：输出 `deduction_total=Σpenalty[severity]`（penalty 从 active 规则包读取，废弃硬编码 `PENALTY`），删除 `grade_for()` 及调用，`risk_level` 口径不变；验证：`test_engine.py` 中单条 agent high → `deduction_total=20`、`risk_level=high`、无 grade
- [x] 2.2 改造 `offline_qc_service.py`：`QcRunResult` 增 `deduction_total`/`grade=None`，`to_http`/`to_event`/`to_envelope` 按命名映射输出（HTTP `deduction_total`、事件 `deduction`）；验证：`test_offline_qc.py` 序列化字段断言通过
- [x] 2.3 改造 `app/observability.py`：`emit_check_completed` 的 score 参数改传扣分口径；验证：日志/指标字段断言通过

## 3. 持久化层

- [x] 3.1 改造 `models/qc.py`：四表 ORM 新列 + CHECK，`grade` 列保留恒 NULL；验证：ORM 反射与 DDL 一致，`test_engine`/落库断言通过
- [x] 3.2 改造 `repositories/quality_repo.py`：落库写 `deduction_total` + 派生 `compliance_score=max(0,100-deduction_total)`，违规行写 `points`/`policy_clause`/`policy_text`（静态表填充，`__ac_default__` 兜底无 rule_id 的 AC hit）；验证：落库后一致性断言 `compliance_score == max(0,100-deduction_total)` 成立，每条 score_eligible hit 条款非空
- [x] 3.3 改造 `repositories/rule_repo.py`：读取 active 包 `penalty` 与 `deduction_policy`；`POST /qc/rules/apply` 校验缺 `deduction_policy` 的包判非法（A2009 回退）；验证：版本切换测试通过
- [x] 3.4 改造 `repositories/review_repo.py`：`pending_item`/`insert_review` 携带扣分字段；验证：`test_reviews.py` 队列项断言通过

## 4. HTTP 与事件层

- [x] 4.1 改造 `services/review_service.py`：`submit`/`_finalize_reviewed` 按 D6 语义（DISMISS 归零 compliant、CONFIRM 不变、CORRECT 写 `revised_deduction` 并同步派生 `revised_score`/`compliance_score`），删除 grade 重算，不改 `risk_level`；验证：`test_reviews.py` 三动作断言通过，CORRECT 后 risk 不变
- [x] 4.2 改造 `routers/review.py`：`ReviewBody` 增 `revised_deduction`（>=0，越界 A2008），`revised_score` 灰度保留；验证：A2007/A2008 边界测试通过
- [x] 4.3 改造 `routers/records.py`：响应含 `deduction_total`，移除 `grade` 查询参数；验证：`test_records.py` 通过，带 `?grade=` 请求不按 grade 过滤
- [x] 4.4 改造 `routers/check.py`：batch accepted 项含 `deduction_total`、`grade=null`；验证：batch 响应字段断言通过
- [x] 4.5 改造 `events/publisher.py`：`COMPLETED_KEYS` 扩容可选键 `deduction`，`grade` 恒 null，`COMPLETED_FORBIDDEN_KEYS` 不变；确认信封 `DATA_WHITELIST` 零改动；验证：`test_events_publish.py` 通过，信封 data 不含 deduction
- [x] 4.6 改造 `services/replay_service.py`：`_persist`/`_result_from_record`/`run_result_to_http`/`_publish_terminal_events` 按新口径；验证：`test_replay_api.py` 通过
- [x] 4.7 改造 `mcp/tools.py`：`query_qc_violations` 输出补 `points`/`policy_clause`/`policy_text`，不暴露 `grade`；验证：`test_mcp_call_qc.py` 通过

## 5. 前端

- [x] 5.1 改造 `web/app.js`：台账/详情「合规分」→「扣分」（主显 `deduction_total`），「机检分/人审分」→「机检扣分/人审扣分」，`zeroDeduction` 判断改 `deduction_total===0`，移除 grade 列，误报/改扣分走 CORRECT+`revised_deduction`；验证：浏览器主路径（灌数→开检→详情→人审）手工走查通过

## 6. 下游 data-platform

- [x] 6.1 确认 `services/data-platform/app/services/snapshot.py` 与 `events/subscriber.py` 继续消费派生 `score→compliance_score`，对信封不存在的 `deduction` 零处理；验证：DP 消费 `qc.check.completed` 链路测试通过，`ads_case_loop` 列不变

## 7. DD-02 升版 V1.18

- [x] 7.1 改写 DD-02 §3.1（状态机映射）/§4.0.1.1（库列↔HTTP↔事件）/§4.0.3（Hit 公共对象）/§4.1.4–4.1.6（records 接口）/§4.3（复核接口）/§4.4（规则版本）/§5.4 正文（含 5.4.0/5.4.1）/§6.1（PostgreSQL 表定义）/§8.2（验收断言 1–28）/§9.3（事件 payload 示例）为扣分口径，§0.1 修订记录增 V1.18，登记 D2「qc_result 冻结」偏差为遗留项；验证：文档 diff 覆盖 design.md D9 清单逐项

## 8. 全量测试修复

- [x] 8.1 修复 `services/qc-service/tests/` 全套至扣分口径，验收断言表 1/6/10 按 acceptance delta 更新；覆盖文件：test_engine、test_offline_qc、test_reviews、test_records、test_rules_seed、test_assertions、test_assertions_15_28、test_merge_hits、test_r10、test_events_publish、test_replay_api、test_mcp_call_qc、test_demo_seed、**test_rules**（import `GRADE_THRESHOLDS_V1`/`PENALTY_V1`，随种子改造同步）、**test_dedup_ac**（自有 `_PENALTY` 与 100 减分断言改扣分口径）、**test_models**（断言新列存在）、**test_subscriber**、**test_alerts**（构造行补 `deduction_total`）、**test_llm_reviewer**（`compliance_score==80` 及 envelope data 键断言改扣分口径）；验证：`pytest services/qc-service/tests/ -q` 全绿
- [x] 8.2 验收轮次 3.2/3.3 重跑（断言 1–28 + 新增一致性断言场景：扣分>100 时 compliance_score=0、违规行条款非空、DISMISS 归零）；验证：三项新场景与 28 条断言全部通过

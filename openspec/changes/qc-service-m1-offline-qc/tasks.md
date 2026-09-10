# qc-service-m1-offline-qc tasks

同一 change，16 轮 apply（0.1～3.4）。**一轮只改一层**：禁止同时改检测规则 + HTTP Schema + 事件 payload。任务 ID 与清单原样：`QC-1.1` … `QC-13.3`。QC-11.2 仍是一条任务；3.2 做断言 1–14，3.3 做断言 15–28。QC-13.2 / QC-13.3 不编码、不占轮次。

全局禁止（每条默认继承）：`POST /qc/check` ingest、`WS /qc/check/stream`、`qc.escalate.human`、`qc.llm.degraded` 频道、R14 `POST /kb/search`、缺护栏日志标 `unverified`、用 `session_id` 冒充 `call_id`、WAIT_TEXT 插 `qc_records`、给 `qc_records` 加 `skipped_rules` 列、叠 B0001 到 A2002。

## 1. 轮次 0.1 — 脚手架（一轮只改一层：脚手架）

- [x] QC-1.1 按 DD-02 §3 补齐 `qc-service` 包：`routers/` `services/` `repositories/` `events/` `integrations/`；目录树与 §3 逐文件同名；保留现有 `app/api/health.py` 仅服务根 `/health`；禁止平行 `api/` 业务路由；无 WS 模块。验证：树存在且无 WS 模块、无业务 `app/api/` 第二套。
- [x] QC-1.2 落地 DD-02 §12 全部 `QC_*`（含 `QC_R14_RAG_ENABLED=false`、`QC_DP_*`、`QC_RAG_SENSITIVE_BASE`、`QC_TEXT_READY_RETRY_SEC=30`、`QC_TEXT_READY_RETRY_MAX=20`）；host 不含 `/api/v1`；无 `QC_RESPONSE_TIMEOUT=3`、无 `QC_EVENT_ESCALATE_ENABLED`。验证：默认 settings 断言上述键与 false。
- [x] QC-1.3 FastAPI 依赖：DB session、Redis DB 3、settings、当前 active `qc_rules` 快照；复用 `common/`；禁止服务内再写 LLM/脱敏/错误封装。验证：deps 注入 Redis DB 3。
- [x] QC-1.4 统一包 `{"code","message","data","trace_id"}`；A2001–A2011 HTTP 按 DD-02 §4.0.4（A2002=422 不叠 B0001；A2001/A2003/A2009/A2010/A2011=200+code；A2004/A2008=400；A2005/A2007=404；A2006=409；B0002=401；B0003=403）。业务成败以 `code==="0"` 为准。验证：非法入参 422 A2002；HTTP 200+A2003 仍为业务失败。

## 2. 轮次 0.2 — 数据层（一轮只改一层：数据层）

- [ ] QC-2.1 5 表 ORM：`qc_records` `qc_violations` `qc_alerts` `qc_reviews` `qc_rules`；`qc_records.session_id` UNIQUE REFERENCES `call_records(session_id)` 禁止删除；无 `quality_id`；对外主键 `qc_record_id`=`qc_records.id`；列名 `compliance_score`/`risk_level`/`qc_result`∈{compliant,violation,pending_review}、`qc_status`∈{DONE,PENDING_REVIEW,REVIEWED,PARTIAL}（不落 WAIT_TEXT）；禁止把库列改名为 `risk`/`score`。验证：ORM/CHECK 与 step4/01 一致。
- [ ] QC-2.2 Alembic 对齐 step4/01（0011 拆 violations）；qc 禁止 INSERT/UPSERT `call_records`/`dialog_turns`；WAIT_TEXT 不 INSERT `qc_records`。验证：迁移可 upgrade；WAIT_TEXT 路径无新行。
- [ ] QC-2.3 Redis `qc:job:{session_id}` JSON：`status` + 终态 `qc_record_id` + `skipped_rules[]`；TTL=24h；DB 3；状态仅 WAIT_TEXT/RUNNING/DONE（Redis）。另键：`qc:words:version` `qc:rules:active` `qc:replay:run:{session_id}` `qc:llm_degraded`（SET/DEL 无固定 EXPIRE）。验证：写入 Redis DB 3 可读回。
- [ ] QC-2.4 任务终态写 `audit_logs`：`action=qc.skip.recorded`，`resource_id=qc_record_id`，`detail.skipped_rules`；不加 `qc_records` 列、不改 CHECK；空数组也写；GET 详情回源 Redis → audit_logs → `[]`。验证：空 skip 仍有审计行。
- [ ] QC-2.5 active `qc_rules.version=v1.0`：penalty 20/8/2、window 30000、grade 阈值；R14 `params.keywords` 18 词与 DD-02 逐字一致；禁止含「我是法院的」；`QC_DEFAULT_ACTIVE_RULES_VERSION=v1.0`。验证：种子 JSON 含 18 词。

## 3. 轮次 0.3 — 集成+夹具（一轮只改一层：集成客户端）

- [ ] QC-3.1 rag 词库客户端：`GET {QC_RAG_SENSITIVE_BASE}/api/v1/sensitive-words/current.dict`（text/plain）；只拉词库；超时 3s、重试 3×、熔断 2 连保持旧 AC；M1 禁止 `POST /kb/search`。验证：客户端无 search 方法；超时保持旧 AC。
- [ ] QC-3.2 DP qc-source：`GET {QC_DP_BASE}/api/v1/data/sessions/{session_id}/qc-source` → QCSession；字段映射逐行 DD-02 §4.6.1；`result` 必须出键（可 null）；无 `dial_history_24h` 整段省略；`ptp_amount`/`standard_answer_hit` M1 恒 null；超时 5s、重试 3×；qc 不反查 `cases`；DP 不得回调 qc。验证：映射单测。
- [ ] QC-3.3 DP pending-qc：`GET .../api/v1/data/sessions:pending-qc?etl_date=&limit=`；仅返回 session_id 列表；逐个再调 3.2；熔断本轮不补检。验证：列表解析与熔断跳过。
- [ ] QC-13.1 用 Mock/契约测试钉死 qc-source JSON（§4.6.1）；不实现 data-platform 服务；交付 `tests/fixtures/qc_source.json` + 3.2 单测。验证：夹具含 `result` 键；本 change 无 DP ETL 代码。

## 4. 轮次 1.1 — AC+窗口（一轮只改一层：AC）

- [ ] QC-4.1 `ACFilter`：复用 `common.security.sensitive_words` + `guardrails_adapter` 三分类；仅 `speaker=agent` 计入扣分；user 投诉/自杀/停呼不扣分；适配器禁止读 call 护栏放行日志；`ai`→`agent`。验证：user 行 `score_eligible=false`。
- [ ] QC-4.2 `DedupACWindow`：W=`same_word_window_ms` 默认 30000；hit_ts → start_offset_ms → `|turn_index|≤2`；只作用于 AC，不作用于 R01–R15。验证：同词 20s 去重；无时间戳时 turn_index 差 1 丢后一条。

## 5. 轮次 1.2 — 规则（不含 R10）（一轮只改一层：规则引擎）

- [ ] QC-4.3 `RuleEngine.run` 执行 R01–R09、R11–R15；skip 表 DD-02 §5.2.2；收集 `skipped_rules`；skip 不产生 hit、不 PARTIAL、不得称红线已通过；R05 禁止用单通 turns 猜拨次；R13 user 行不进 score；**本轮不做 R10**。验证：缺 `debtor_name` skip R01。
- [ ] QC-4.4 R04：§5.2.4 确认词 + 披露词；确认后披露不打；自称公权力走 R12；不依赖 `debtor_name`。验证：未确认披露命中；「是我」后再披露不命中。
- [ ] QC-4.5 R14：关键词/`standard_answer_hit` 包含比对；无词且无 hit_text → skip R14；不调 rag 检索；severity=medium，默认不告警。验证：无 `/kb/search` 调用。

## 6. 轮次 1.3 — Merge+R10+编排（一轮只改一层：合并与打分）

- [ ] QC-4.6 MergeHits：去重键、跨层同 tag、severity 最高、同级 AC>规则>LLM；保留规则 `rule_id`；LLM `rule_id` 必须 ∈R01–R15 否则丢；user 标 `score_eligible=false`；禁止对未合并列表告警。验证：同 turn AC threat+R11 只扣 20。
- [ ] QC-4.7 R10 在 MergeHits 之后：应转证据 + `result` 存在且 `≠escalated` → 一条 session 级 R10；无 `result` → skip，禁止 unverified 充数；不读护栏日志。验证：无 result 时 skipped_rules 含 R10 且无 unverified 行。
- [ ] QC-4.8 `OfflineQcService` 顺序 ①AC ②窗口 ③规则 ④LLM ⑤Merge ⑥R10 ⑦score ⑧告警/事件；`ReplayService`；扣分仅 `score_eligible`：high−20/med−8/low−2；落库 `risk_level`；HTTP `compliance_score`/`risk`，事件 `score`/`risk`；agent+high → `risk_level=high` 且 `qc_result=pending_review` + `PENDING_REVIEW`；有 agent 违规但无 high → `qc_result=violation` + `DONE`；无 agent 违规 → `compliant` + `DONE`；`grade` 与 `risk_level` 独立，禁止用分数切 `risk_level`；未齐未耗尽只 Redis WAIT_TEXT；RETRY_MAX 耗尽 → PARTIAL+A2003；崩溃 → PARTIAL+A2001 HTTP 200；raw 无 `call_records`→A2003 不插表。验证：单条高危 score=80、risk=high、grade=PASS。

## 7. 轮次 1.4 — LLM+脱敏（一轮只改一层：LLM）

- [ ] QC-5.1 `common/llm/prompts/qc_review.yml` version=`qc_review_v1`，约束抄 DD-02 §13.2；不得因缺护栏日志/未检索知识库输出 R10/unverified 或 R14；user 投诉自杀不是坐席违规。验证：yml version 与约束句存在。
- [ ] QC-5.2 `LLMReviewer.review(turns)` → JSON→Pydantic；evidence 的 turn_id 必须存在；reason 不含编造金额；走 `common.llm`；落 `llm_call_logs`（`qc_review_exemption`）；不做 confidence&lt;0.7 门控；超时 5s、重试 3×。验证：非法 turn_id 丢弃。
- [ ] QC-5.3 连续 3 次超时/5xx → OPEN：跳过 LLM，仍出 AC+规则分；`SET qc:llm_degraded`；发布 `system.alert.raised`（`event=qc:llm_degraded`，`error_code=A2011`）；HTTP 200 + code=A2011；频道不是 `qc.llm.degraded`；60s 半开。验证：无 `qc.llm.degraded` 频道消息。
- [ ] QC-10.3 `evidence_text` 落库/API/事件经 `common.security.mask`；不含完整手机号/身份证；逾期金额不入质检 LLM。验证：落表无完整手机号。

## 8. 轮次 2.1 — replay API（一轮只改一层：离线入口 HTTP）

- [ ] QC-6.1 `POST /api/v1/qc/replay/{session_id}`：内部 GET qc-source，禁止 Body 传 turns；路径参数只当 `session_id`，禁止写入事件 `call_id`；未齐/未结束且重试未耗尽 → A2003 HTTP 200 只 Redis WAIT_TEXT 不插表；RETRY_MAX 耗尽 → INSERT PARTIAL+A2003；`call_records` 无 → A2003 不插表；已 REVIEWED 无 Force-Rerun → A2002 HTTP 422；成功 QcRunResult 不强制 `skipped_rules`。验证：未齐 replay 无 qc_records 行。
- [ ] QC-6.2 `POST /api/v1/qc/replay/raw` 扁平 QCSession；`X-Idempotency-Key` 必填；缺可选元数据规则 skip；`call_ended≠true`/turns 空/角色错 → A2002 HTTP 422；无主数据 → A2003；限流 10/s。验证：无 call_records → A2003 不插表。
- [ ] QC-6.3 `POST /api/v1/qc/check/batch` ≤100 通已结束文本齐；>100 → A2004 HTTP 400；部分失败进 rejected[]；无 `POST /qc/check` ingest。验证：101 通整单 A2004。

## 9. 轮次 2.2 — 台账/health（一轮只改一层：台账与可观测 HTTP）

- [ ] QC-6.4 `GET /qc/records/{session_id}`、`GET /qc/records`、`GET /qc/records/stats`；详情必须 `qc_record_id` + `skipped_rules[]`；HTTP 用 `risk`/`compliance_score`；仅详情无行 A2005 HTTP 404；列表/stats 无匹配 → 200 + `items=[]`，禁止 404；`group_by` 非法 A2002 HTTP 422；evidence/phone 脱敏；page_size≤100。验证：空列表 200 空数组。
- [ ] QC-6.5 前缀 `/api/v1`；`GET /health` 挂服务根（不在 `/api/v1`）；无 `POST /qc/check`、无 WS；`X-Trace-Id` 缺则生成并回写。验证：`/health` 在根；`/api/v1/qc/check` 无 ingest。
- [ ] QC-10.1 `GET /health`：`ac_version` `llm_degraded` `rag_connected`；无鉴权；不用 A200x；任一带依赖失败则 `status=degraded`（绑定已有字段，不新造键）。验证：`llm_degraded=true` 时 status=degraded。
- [ ] QC-10.2 DD-02 §15.3 埋点：started/wait_text/completed、llm.review.*、rag.words.*、qc.alert.raised；无 `qc.escalate.triggered`、无 `mode=realtime`。验证：started 的 mode=offline。

## 10. 轮次 2.3 — 事件发布（一轮只改一层：6 频道 payload）

- [ ] QC-7.1 发布 6 频道，payload 逐字段同构 DD-02 §9.3 / step4/03：`qc.alert.raised`；`qc.check.completed` 含 `score`←`compliance_score`、`risk`←`risk_level`、`call_id` 可 null（禁止 session_id/replay 路径冒充）；`qc.complaint.suspected` **仅** agent+high+`rule_id∈{R11,R12,R13}`（R05 即使 tag=threat 也不发）；`qc.stop_call.detected`；`qc.review.done` 含 `sample_type`∈{positive,negative,corrected}；`system.alert.raised` 见 QC-5.3；一律 Unix `ts` + `trace_id`；**不发布** `qc.escalate.human`。验证：订阅 escalate 无消息；仅 R05 不发 suspected。

## 11. 轮次 2.4 — 事件订阅（一轮只改一层：订阅与词库）

- [ ] QC-7.2 订阅 `call.session.ended` / `etl.task.done` / `kb.sensitive_updated` / `strategy.updated`；ended/etl → GET qc-source；未齐 WAIT_TEXT；M1 可缺 ended；禁止 DP HTTP 调 qc。验证：未齐 ended 只写 Redis。
- [ ] QC-7.3 `WordLoader`：启动 + 每小时 + `kb.sensitive_updated` 全量拉 dict 重建 AC；不用 `since_v`；写 `qc:words:version`。验证：收到 kb 事件后 version 更新。

## 12. 轮次 2.5 — 告警/复核/规则（一轮只改一层：告警复核 HTTP）

- [ ] QC-8.1 `AlertService.on_high_risk`：仅最终 hits 且 agent 且 high；user high、R05 low、未合并重复不写 `qc_alerts`（CHECK `level=high`）；订阅方=事后主管，禁止通话页。验证：R05 无告警行。
- [ ] QC-8.2 `GET /qc/alerts`、`POST /qc/alerts/{alert_id}/ack`；不存在或已 ACK → A2006 HTTP 409；未认证 B0002=401。验证：二次 ack 409。
- [ ] QC-8.3 `GET /qc/reviews/pending`、`POST /qc/reviews/{qc_record_id}` CONFIRM/DISMISS/CORRECT；路径参数是 qc_record_id；分数越界 A2008 HTTP 400；不存在/已 REVIEWED → A2007 HTTP 404；`sample_type`：CONFIRM→positive，DISMISS→negative，CORRECT→corrected。验证：CORRECT 后 qc_status=REVIEWED。
- [ ] QC-8.4 `GET /qc/rules/active`、`POST /qc/rules/apply` 原子切换；版本非法 → A2009 HTTP 200 回退上一版；事务失败 → A2010 HTTP 200 不切换；全库仅一版 active。验证：非法 version 仍为上一 active。

## 13. 轮次 3.1 — MCP+引擎测（一轮只改一层：MCP 与引擎单测）

- [ ] QC-9.1 实现 `query_qc_violations` / `query_qc_alerts`（入参 case_id+session_id，出参带 qc_record_id）；不实现 `query_call_asr` / `query_call_sessions`；投诉节点不直连 qc HTTP。验证：工具列表仅两查询。
- [ ] QC-11.1 引擎单测：AC 窗口、MergeHits、R04/R10/R14 skip、user 不扣分；MOCK_LLM=1；覆盖断言 6、19、20、22–25、27。验证：`tests/test_engine*.py` 通过。

## 14. 轮次 3.2 — 断言 1–14（一轮只改一层：验收前半）

- [ ] QC-11.2 将 DD-02 §8.2 断言 **1–14** 做成可执行用例（可分文件；任务 ID 不拆）。含断言 5：订阅 `qc.escalate.human` 无消息。本轮不实现 15–28。验证：断言 1–14 全部可执行通过。

## 15. 轮次 3.3 — 断言 15–28 + skip 标记（一轮只改一层：验收后半）

- [ ] QC-11.2 将 DD-02 §8.2 断言 **15–28** 做成可执行用例（同一任务 ID 续作）。断言 23/24：无 rag `/kb/search` 调用。断言 26 可用 Mock DP。断言 28 DEL Redis 后 GET 仍回 skipped_rules。验证：断言 15–28 全部可执行通过。
- [ ] QC-11.4 明确跳过：RAG 标准答案检索、红线 6 护栏日志、实时 WS；不测 `/kb/search`；不测 `guardrail.pass`；红线 6 非 M1 必过。验证：测试 README 或 pytest skip 标记存在。

## 16. 轮次 3.4 — 冒烟+部署（一轮只改一层：冒烟与镜像）

- [ ] QC-11.3 `scripts/smoke/smoke_qc.sh`：health → seed 通话 → replay/raw → records/alerts；依赖 rag+DP+PG+Redis；不起 call-service。验证：脚本在种子环境跑通。
- [ ] QC-12.1 完善 `services/qc-service/Dockerfile`，暴露 8003，启动拉词库；HEALTHCHECK `/health`。验证：镜像 HEALTHCHECK 指向根 `/health`。
- [ ] QC-12.2 compose/env 注入 §12 配置；Redis 指定 DB 3；`QC_R14_RAG_ENABLED=false`；无实时质检超时项。验证：env 含 DB 3 与 R14 flag false。

## 17. 阶段 4 — M2+ 登记（不编码、不占轮次）

- [ ] QC-13.2 【M2+ 不编码】R14 标准答案检索 `POST /api/v1/kb/search` 仅登记风险；M1 `QC_R14_RAG_ENABLED` 保持 false。验证：无 search 客户端代码。
- [ ] QC-13.3 【M2+ 不编码】call 护栏放行日志 `audit_logs.action=guardrail.pass` 仅登记；缺记录仍禁止 unverified 充数。验证：无 guardrail.pass 读取代码。

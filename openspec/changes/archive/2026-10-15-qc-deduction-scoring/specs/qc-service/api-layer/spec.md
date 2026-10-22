## MODIFIED Requirements

### Requirement: 仅离线 HTTP 表面

业务路由 SHALL 前缀 `/api/v1`。`GET /health` SHALL 挂在服务根（`http://qc-service:8003/health`），MUST NOT 注册在 `/api/v1` 下。缺 `X-Trace-Id` SHALL 生成并回写。服务 MUST NOT 注册 `POST /qc/check` ingest 或任何 WebSocket 质检路径。

HTTP JSON SHALL 遵循 DD-02 §4.0.1.1 的 V1.18 口径：`deduction_total`（主口径）、`compliance_score`（派生兼容）、`risk`（不是 `risk_level`）、`qc_result`、`qc_status`、`grade`（恒 null）。库列仍为 `deduction_total` / `compliance_score` / `risk_level` / `qc_result` / `qc_status` / `grade`。replay 路径参数 SHALL 仅为 `session_id`，MUST NOT 写入事件 `call_id`。业务成功 SHALL 为 `code==="0"`；HTTP 200 + A2003 SHALL 仍为业务失败。A2002 SHALL 为 HTTP 422 且不叠 B0001。A2011 SHALL 为 HTTP 200、`code=A2011`，且 `data` 仍含 AC+规则扣分结果（DD-02 相对通用失败 `data=null` 的例外）。

#### Scenario: 无 ingest 路由

- **WHEN** 客户端 POST `/api/v1/qc/check`
- **THEN** 未实现 ingest

#### Scenario: 路径 session_id 不是 call_id

- **WHEN** `POST /qc/replay/{session_id}` 成功并发布 `qc.check.completed`
- **THEN** payload `call_id` 为 `QCSession.call_id` 或 JSON null，绝不是路径 `session_id`

### Requirement: POST 按 session_id replay

`POST /api/v1/qc/replay/{session_id}` SHALL 内部 GET qc-source，MUST NOT 在 Body 接受 turns。未结束/全文未齐且重试未耗尽时，API SHALL 返回 HTTP 200 `code=A2003`，只写 Redis WAIT_TEXT，MUST NOT INSERT `qc_records`。`QC_TEXT_READY_RETRY_MAX` 耗尽 SHALL INSERT `PARTIAL` 并返回 A2003。无 `call_records` SHALL 返回 A2003 且不插表。已 REVIEWED 且无合法 `X-QC-Force-Rerun` SHALL 返回 A2002 HTTP 422。成功 `data` SHALL 为 QcRunResult，含 `qc_record_id`、`session_id`、`case_id`、`deduction_total`、`compliance_score`、`risk`、`grade`（null）、`qc_result`、`qc_status`、`hits`（DD-02 §4.0.3），MUST NOT 强制 `skipped_rules`。HTTP MUST NOT 用 `score` 或 `deduction` 作为 QcRunResult 字段。

#### Scenario: 未齐 replay 只等 Redis

- **WHEN** 对未结束或全文未齐且重试未耗尽的会话调用 replay
- **THEN** HTTP 200 `code=A2003`，Redis WAIT_TEXT，无 `qc_records` 行

### Requirement: 列表与 stats 空结果为 200

`GET /api/v1/qc/records/{session_id}` SHALL 返回 `qc_record_id` 与 `skipped_rules[]`（回源：Redis → audit_logs → `[]`）。HTTP SHALL 使用 `risk`、`deduction_total` 与 `compliance_score`。详情 hits SHALL 含 `source`、`points`、`policy_clause`、`policy_text`、可选 `word` 与检索片段字段。详情无行 SHALL 仅为 A2005 HTTP 404。

`GET /api/v1/qc/records` 与 `GET /api/v1/qc/records/stats` 无匹配 SHALL 返回 HTTP 200 且空 `items` 数组，不是 A2005。非法 `group_by` SHALL 为 A2002 HTTP 422。列表 `page_size` MUST NOT 超过 100。列表 MUST NOT 提供 `grade` 过滤参数。evidence/phone SHALL 脱敏。

#### Scenario: 空列表不是 A2005

- **WHEN** GET `/qc/records` 无匹配
- **THEN** HTTP 200 `code="0"`，`items=[]`，`total=0`

#### Scenario: 空 stats 不是 A2005

- **WHEN** GET `/qc/records/stats` 窗口内无行
- **THEN** HTTP 200 且 `items=[]`

#### Scenario: 详情无行才是 A2005

- **WHEN** GET `/qc/records/{session_id}` 无行
- **THEN** HTTP 404 `code=A2005`

#### Scenario: grade 过滤参数不存在

- **WHEN** GET `/qc/records?grade=PASS`
- **THEN** 该参数不被识别（A2002 或被忽略），列表不按 grade 过滤

### Requirement: 复核 HTTP 使用 qc_record_id

`GET /api/v1/qc/reviews/pending` 与 `POST /api/v1/qc/reviews/{qc_record_id}` SHALL 使用路径参数 `qc_record_id`。动作 SHALL 为 CONFIRM/DISMISS/CORRECT。CORRECT SHALL 接受 `revised_deduction`（整数，`>=0`）；越界 SHALL 为 A2008 HTTP 400。不存在或已 REVIEWED SHALL 为 A2007 HTTP 404。`sample_type` 映射 SHALL 为 CONFIRM→positive、DISMISS→negative、CORRECT→corrected。

复核语义 SHALL 为：DISMISS（误报）→ `deduction_total=0` 且 `qc_result=compliant`；CONFIRM → `deduction_total` 不变；CORRECT → `deduction_total=revised_deduction`。三种动作均置 `qc_status=REVIEWED`，且 MUST NOT 改 `risk_level`（risk 由 hits 决定，复核不重跑引擎）。CORRECT 时 `revised_score` 与 `compliance_score` SHALL 同步写派生值 `max(0, 100 - revised_deduction)` / `max(0, 100 - deduction_total)`，一致性断言 MUST 恒成立。

#### Scenario: CORRECT 更新分数

- **WHEN** POST 复核 CORRECT 且 `revised_deduction` 合法
- **THEN** `qc_status=REVIEWED` 且 `deduction_total` 等于 `revised_deduction`，`compliance_score == max(0, 100 - revised_deduction)`，`risk_level` 不变

#### Scenario: DISMISS 归零为合规

- **WHEN** POST 复核 DISMISS
- **THEN** `deduction_total=0`、`qc_result=compliant`、`qc_status=REVIEWED`、`compliance_score=100`

### Requirement: hits 含 source、word 与条款

`GET /api/v1/qc/records/{session_id}` 与 replay 成功 `data.hits` SHALL 为每条命中提供 `source`（`ac` | `rule` | `llm`）、本条扣分 `points`、制度条款号 `policy_clause`、制度条款原文 `policy_text`、可选 `word`（AC 命中词）、以及 RAG 检索片段字段 `kb_snippet`（无检索则为空/省略）。`policy_text` 与 `kb_snippet` MUST 为两个独立字段。`skipped_rules` 语义不变。evidence 仍 MUST 脱敏。

#### Scenario: 详情可区分 AC 与规则

- **WHEN** 一通同时有 AC 词命中与 R12 规则命中
- **THEN** hits 中至少一条 `source=ac` 且一条 `source=rule`，两条均含 `points` 与 `policy_clause`/`policy_text`

### Requirement: 演示人审误报走 CORRECT

`POST /api/v1/qc/reviews/{qc_record_id}` 仍接受 CONFIRM / DISMISS / CORRECT。演示前端误报 MUST 走 `CORRECT` 且 `revised_deduction` 为 `>=0` 整数；缺值或越界 SHALL A2008。`DISMISS` MAY 保留 API 但不作为演示按钮。CONFIRM MUST NOT 改扣分。已 REVIEWED SHALL A2007。

#### Scenario: 误报改分

- **WHEN** POST `action=CORRECT` 且 `revised_deduction=0`
- **THEN** `qc_status=REVIEWED` 且 `deduction_total=0`、`qc_result=compliant`

## Purpose

定义 qc-service M1 HTTP 管理面：离线 replay/raw/batch、台账、告警、复核、规则版本切换，前缀 `/api/v1`，信封按 A2 映射。

## Requirements

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

### Requirement: raw 限流委托共享固定窗口零件

`POST /api/v1/qc/replay/raw` 的限流 MUST 委托共享固定窗口零件（`common.resilience.rate_limit.allow`），不得再走路由内私有 Redis INCR。可观测行为 MUST 为：每用户每秒 10 次（key `qc:ratelimit:raw:{user}`，用户维优先 `X-User-Id` 头，回退客户端 IP，皆无则 `anon`）；窗口 1 秒；超限抛业务错误 B0006（由异常处理器转现有 HTTP 契约）。调用 `allow` 时 MUST 传入 `context`，其中至少含本次请求 `trace_id`，以便 `qc.rate_limit.hit` 可追踪。Redis 不可用或超时时 MUST 静默放行，MUST NOT 因此返回 B0006。

#### Scenario: 超限 10/s 返回 B0006

- **WHEN** 同一用户维在 1 秒内第 11 次调用 `POST /api/v1/qc/replay/raw`
- **THEN** 该请求被拒绝，错误码为 B0006，与改造前超限契约一致

#### Scenario: Redis 不可用静默放行

- **WHEN** 限流所用 Redis 不可用或超时
- **THEN** 请求不被限流拒绝，不因 Redis 故障返回 B0006

#### Scenario: 限流拒绝事件带 trace_id

- **WHEN** raw 限流超限且请求带 `X-Trace-Id`（或中间件生成的 trace_id）
- **THEN** `qc.rate_limit.hit` payload 含该 `trace_id`

### Requirement: POST replay/raw

`POST /api/v1/qc/replay/raw` SHALL 接受扁平 QCSession。`X-Idempotency-Key` SHALL 必填。限流 SHALL 为 10/s（委托共享固定窗口零件，见「raw 限流委托共享固定窗口零件」）。缺可选元数据 SHALL skip 依赖规则，不是 A2002。`call_ended≠true` / 空 turns / 非法 speaker / 缺 session_id 或 case_id SHALL 返回 A2002 HTTP 422。该 session_id 无 `call_records` SHALL 返回 A2003 且不插表。

#### Scenario: raw 无通话主数据

- **WHEN** raw replay 的 session_id 不在 `call_records`
- **THEN** HTTP 200 `code=A2003`，文案要求先有通话主数据，且不插 `qc_records`

### Requirement: POST check/batch

`POST /api/v1/qc/check/batch` SHALL 最多接受 100 条已结束且全文齐的会话。数量 >100 SHALL 整单返回 A2004 HTTP 400。部分失败 SHALL 进入 `rejected[]`，含 `session_id` 与 `code`。该路径是批量，不是实时 ingest。

#### Scenario: 超限批次整单拒绝

- **WHEN** `session_ids.length>100`
- **THEN** HTTP 400 `code=A2004`，不处理任何会话

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

### Requirement: 告警 HTTP 给事后主管

`GET /api/v1/qc/alerts` 与 `POST /api/v1/qc/alerts/{alert_id}/ack` SHALL 服务事后主管，不是通话页。不存在或已 ACK 的 ack SHALL 返回 A2006 HTTP 409。未认证 SHALL 返回 B0002 HTTP 401。

告警写入（`AlertService.on_high_risk`）SHALL 仅针对 MergeHits 终态行且 `speaker=agent` 且 `severity=high`。用户 high、R05 low、未合并重复 MUST NOT INSERT `qc_alerts`（`level` CHECK 仅为 `high`）。

#### Scenario: 重复 ack 冲突

- **WHEN** 对不存在或已 ACK 的告警 POST ack
- **THEN** HTTP 409 `code=A2006`

#### Scenario: R05 不建告警

- **WHEN** 仅有 R05 hits
- **THEN** `qc_alerts` 无新行，GET `/qc/alerts` 不为该会话列出 NEW 告警

### Requirement: 复核 HTTP 使用 qc_record_id

`GET /api/v1/qc/reviews/pending` 与 `POST /api/v1/qc/reviews/{qc_record_id}` SHALL 使用路径参数 `qc_record_id`。动作 SHALL 为 CONFIRM/DISMISS/CORRECT。CORRECT SHALL 接受 `revised_deduction`（整数，`>=0`）；越界 SHALL 为 A2008 HTTP 400。不存在或已 REVIEWED SHALL 为 A2007 HTTP 404。`sample_type` 映射 SHALL 为 CONFIRM→positive、DISMISS→negative、CORRECT→corrected。

复核语义 SHALL 为：DISMISS（误报）→ `deduction_total=0` 且 `qc_result=compliant`；CONFIRM → `deduction_total` 不变；CORRECT → `deduction_total=revised_deduction`。三种动作均置 `qc_status=REVIEWED`，且 MUST NOT 改 `risk_level`（risk 由 hits 决定，复核不重跑引擎）。CORRECT 时 `revised_score` 与 `compliance_score` SHALL 同步写派生值 `max(0, 100 - revised_deduction)` / `max(0, 100 - deduction_total)`，一致性断言 MUST 恒成立。

#### Scenario: CORRECT 更新分数

- **WHEN** POST 复核 CORRECT 且 `revised_deduction` 合法
- **THEN** `qc_status=REVIEWED` 且 `deduction_total` 等于 `revised_deduction`，`compliance_score == max(0, 100 - revised_deduction)`，`risk_level` 不变

#### Scenario: DISMISS 归零为合规

- **WHEN** POST 复核 DISMISS
- **THEN** `deduction_total=0`、`qc_result=compliant`、`qc_status=REVIEWED`、`compliance_score=100`

### Requirement: 规则版本切换

`GET /api/v1/qc/rules/active` SHALL 返回唯一 active 包。`POST /api/v1/qc/rules/apply` SHALL 原子切换。非法版本 SHALL 返回 A2009 HTTP 200 并回退上一版。事务失败 SHALL 返回 A2010 HTTP 200 且不切换。MUST 仅一版 active。

#### Scenario: 非法版本回退

- **WHEN** apply 目标版本不存在或阈值非法
- **THEN** HTTP 200 `code=A2009`，上一版 active 保持

### Requirement: CORS 放行本地静态页

qc-service SHALL 允许来源 `http://localhost:8080` 与 `http://127.0.0.1:8080` 的浏览器跨域访问业务 API（MAY 额外放行 5500，与投诉页一致）。未放行源 MUST NOT 作为演示主路径依赖。

#### Scenario: 8080 可跨域调用

- **WHEN** 浏览器页托管在 localhost:8080 并请求 8003 `/api/v1/qc/demo/pending`
- **THEN** CORS 预检或响应允许该源，请求不被浏览器同源策略拒绝（服务侧放行该 Origin）

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

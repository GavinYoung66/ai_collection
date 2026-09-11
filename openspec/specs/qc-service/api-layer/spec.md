## Purpose

定义 qc-service M1 HTTP 管理面：离线 replay/raw/batch、台账、告警、复核、规则版本切换，前缀 `/api/v1`，信封按 A2 映射。

## Requirements

### Requirement: 仅离线 HTTP 表面

业务路由 SHALL 前缀 `/api/v1`。`GET /health` SHALL 挂在服务根（`http://qc-service:8003/health`），MUST NOT 注册在 `/api/v1` 下。缺 `X-Trace-Id` SHALL 生成并回写。服务 MUST NOT 注册 `POST /qc/check` ingest 或任何 WebSocket 质检路径。

HTTP JSON SHALL 遵循 DD-02 §4.0.1.1：`compliance_score`（不是 `score`）、`risk`（不是 `risk_level`）、`qc_result`、`qc_status`、`grade`。库列仍为 `compliance_score` / `risk_level` / `qc_result` / `qc_status` / `grade`。replay 路径参数 SHALL 仅为 `session_id`，MUST NOT 写入事件 `call_id`。业务成功 SHALL 为 `code==="0"`；HTTP 200 + A2003 SHALL 仍为业务失败。A2002 SHALL 为 HTTP 422 且不叠 B0001。A2011 SHALL 为 HTTP 200、`code=A2011`，且 `data` 仍含 AC+规则分（DD-02 相对通用失败 `data=null` 的例外）。

#### Scenario: 无 ingest 路由

- **WHEN** 客户端 POST `/api/v1/qc/check`
- **THEN** 未实现 ingest

#### Scenario: 路径 session_id 不是 call_id

- **WHEN** `POST /qc/replay/{session_id}` 成功并发布 `qc.check.completed`
- **THEN** payload `call_id` 为 `QCSession.call_id` 或 JSON null，绝不是路径 `session_id`

### Requirement: POST 按 session_id replay

`POST /api/v1/qc/replay/{session_id}` SHALL 内部 GET qc-source，MUST NOT 在 Body 接受 turns。未结束/全文未齐且重试未耗尽时，API SHALL 返回 HTTP 200 `code=A2003`，只写 Redis WAIT_TEXT，MUST NOT INSERT `qc_records`。`QC_TEXT_READY_RETRY_MAX` 耗尽 SHALL INSERT `PARTIAL` 并返回 A2003。无 `call_records` SHALL 返回 A2003 且不插表。已 REVIEWED 且无合法 `X-QC-Force-Rerun` SHALL 返回 A2002 HTTP 422。成功 `data` SHALL 为 QcRunResult，含 `qc_record_id`、`session_id`、`case_id`、`compliance_score`、`risk`、`grade`、`qc_result`、`qc_status`、`hits`（DD-02 §4.0.3），MUST NOT 强制 `skipped_rules`。HTTP MUST NOT 用 `score` 作为 QcRunResult 合规分字段。

#### Scenario: 未齐 replay 只等 Redis

- **WHEN** 对未结束或全文未齐且重试未耗尽的会话调用 replay
- **THEN** HTTP 200 `code=A2003`，Redis WAIT_TEXT，无 `qc_records` 行

### Requirement: POST replay/raw

`POST /api/v1/qc/replay/raw` SHALL 接受扁平 QCSession。`X-Idempotency-Key` SHALL 必填。限流 SHALL 为 10/s。缺可选元数据 SHALL skip 依赖规则，不是 A2002。`call_ended≠true` / 空 turns / 非法 speaker / 缺 session_id 或 case_id SHALL 返回 A2002 HTTP 422。该 session_id 无 `call_records` SHALL 返回 A2003 且不插表。

#### Scenario: raw 无通话主数据

- **WHEN** raw replay 的 session_id 不在 `call_records`
- **THEN** HTTP 200 `code=A2003`，文案要求先有通话主数据，且不插 `qc_records`

### Requirement: POST check/batch

`POST /api/v1/qc/check/batch` SHALL 最多接受 100 条已结束且全文齐的会话。数量 >100 SHALL 整单返回 A2004 HTTP 400。部分失败 SHALL 进入 `rejected[]`，含 `session_id` 与 `code`。该路径是批量，不是实时 ingest。

#### Scenario: 超限批次整单拒绝

- **WHEN** `session_ids.length>100`
- **THEN** HTTP 400 `code=A2004`，不处理任何会话

### Requirement: 列表与 stats 空结果为 200

`GET /api/v1/qc/records/{session_id}` SHALL 返回 `qc_record_id` 与 `skipped_rules[]`（回源：Redis → audit_logs → `[]`）。HTTP SHALL 使用 `risk` 与 `compliance_score`。详情无行 SHALL 仅为 A2005 HTTP 404。

`GET /api/v1/qc/records` 与 `GET /api/v1/qc/records/stats` 无匹配 SHALL 返回 HTTP 200 且空 `items` 数组，不是 A2005。非法 `group_by` SHALL 为 A2002 HTTP 422。列表 `page_size` MUST NOT 超过 100。evidence/phone SHALL 脱敏。

#### Scenario: 空列表不是 A2005

- **WHEN** GET `/qc/records` 无匹配
- **THEN** HTTP 200 `code="0"`，`items=[]`，`total=0`

#### Scenario: 空 stats 不是 A2005

- **WHEN** GET `/qc/records/stats` 窗口内无行
- **THEN** HTTP 200 且 `items=[]`

#### Scenario: 详情无行才是 A2005

- **WHEN** GET `/qc/records/{session_id}` 无行
- **THEN** HTTP 404 `code=A2005`

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

`GET /api/v1/qc/reviews/pending` 与 `POST /api/v1/qc/reviews/{qc_record_id}` SHALL 使用路径参数 `qc_record_id`。动作 SHALL 为 CONFIRM/DISMISS/CORRECT。分数越界 SHALL 为 A2008 HTTP 400。不存在或已 REVIEWED SHALL 为 A2007 HTTP 404。`sample_type` 映射 SHALL 为 CONFIRM→positive、DISMISS→negative、CORRECT→corrected。

#### Scenario: CORRECT 更新分数

- **WHEN** POST 复核 CORRECT 且 revised_score 在 0–100
- **THEN** `qc_status=REVIEWED` 且 `compliance_score` 等于修正分

### Requirement: 规则版本切换

`GET /api/v1/qc/rules/active` SHALL 返回唯一 active 包。`POST /api/v1/qc/rules/apply` SHALL 原子切换。非法版本 SHALL 返回 A2009 HTTP 200 并回退上一版。事务失败 SHALL 返回 A2010 HTTP 200 且不切换。MUST 仅一版 active。

#### Scenario: 非法版本回退

- **WHEN** apply 目标版本不存在或阈值非法
- **THEN** HTTP 200 `code=A2009`，上一版 active 保持

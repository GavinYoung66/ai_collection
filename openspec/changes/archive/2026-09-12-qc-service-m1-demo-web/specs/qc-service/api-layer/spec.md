## ADDED Requirements

### Requirement: CORS 放行本地静态页

qc-service SHALL 允许来源 `http://localhost:8080` 与 `http://127.0.0.1:8080` 的浏览器跨域访问业务 API（MAY 额外放行 5500，与投诉页一致）。未放行源 MUST NOT 作为演示主路径依赖。

#### Scenario: 8080 可跨域调用

- **WHEN** 浏览器页托管在 localhost:8080 并请求 8003 `/api/v1/qc/demo/pending`
- **THEN** CORS 预检或响应允许该源，请求不被浏览器同源策略拒绝（服务侧放行该 Origin）

### Requirement: hits 含 source、word 与条款

`GET /api/v1/qc/records/{session_id}` 与 replay 成功 `data.hits` SHALL 为每条命中提供 `source`（`ac` | `rule` | `llm`）、可选 `word`（AC 命中词）、以及检索条款字段（无检索则为空/省略）。`skipped_rules` 语义不变。evidence 仍 MUST 脱敏。

#### Scenario: 详情可区分 AC 与规则

- **WHEN** 一通同时有 AC 词命中与 R12 规则命中
- **THEN** hits 中至少一条 `source=ac` 且一条 `source=rule`

### Requirement: 演示人审误报走 CORRECT

`POST /api/v1/qc/reviews/{qc_record_id}` 仍接受 CONFIRM / DISMISS / CORRECT。演示前端误报 MUST 走 `CORRECT` 且 `revised_score` 为 0–100 整数；缺分或越界 SHALL A2008。`DISMISS` MAY 保留 API 但不作为演示按钮。CONFIRM MUST NOT 改分。已 REVIEWED SHALL A2007。

#### Scenario: 误报改分

- **WHEN** POST `action=CORRECT` 且 `revised_score=100`
- **THEN** `qc_status=REVIEWED` 且 `compliance_score=100`

## MODIFIED Requirements

### Requirement: 列表与 stats 空结果为 200

`GET /api/v1/qc/records/{session_id}` SHALL 返回 `qc_record_id` 与 `skipped_rules[]`（回源：Redis → audit_logs → `[]`）。HTTP SHALL 使用 `risk` 与 `compliance_score`。详情 hits SHALL 含 `source`、可选 `word` 与条款字段。详情无行 SHALL 仅为 A2005 HTTP 404。

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

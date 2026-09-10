## Purpose

定义 qc-service M1 可观测性：服务根健康检查三字段、埋点事件，以及证据在落库/API/事件中的 PII 脱敏。

## ADDED Requirements

### Requirement: 服务根 GET /health 三字段

`GET /health` SHALL 挂载于 `http://qc-service:8003/health`，MUST NOT 注册在 `/api/v1` 下。SHALL 无鉴权，MUST NOT 使用 A200x 业务码。响应 SHALL 使用统一信封 `{"code","message","data","trace_id"}`，业务成功 `code==="0"`。`data` SHALL 含 `ac_version`、`llm_degraded`、`rag_connected`。任一带依赖失败（`llm_degraded=true` 或 `rag_connected=false` 或词库探活无法得到有效 `ac_version`）时 `data.status` SHALL 为 `degraded`；否则为 `ok` 或 `healthy`。API MUST NOT 新造状态键。`degraded` SHALL 只绑定上述已有字段。

#### Scenario: 依赖成功则为健康

- **WHEN** AC 版本可用、LLM 熔断关闭、rag 词库可达
- **THEN** `data.status` 为 `ok` 或 `healthy`，且三字段存在

#### Scenario: degraded 绑定已有字段

- **WHEN** `llm_degraded=true` 或 `rag_connected=false`
- **THEN** `data.status=degraded`，不增加新键

#### Scenario: health 不在 /api/v1

- **WHEN** 客户端调用 `GET /api/v1/health`
- **THEN** 该路径不是探活入口；无鉴权探活的权威路径是服务根 `GET /health`

### Requirement: 埋点事件且无实时模式

系统 SHALL 发出 DD-02 §15.3 事件：`qc.check.started`、`qc.check.wait_text`、`qc.check.completed`、`llm.review.started`、`llm.review.completed`、`llm.review.degraded`、`rag.words.loaded`、`rag.words.stale`、`qc.alert.raised`。`qc.check.started` 的 mode SHALL 为 `offline`。系统 MUST NOT 发出 `qc.escalate.triggered`，MUST NOT 发出 `mode=realtime`。`rag.words.stale` 时钟 SHALL 遵循 DD-02 §15.3（60s 热加载 SLA，2h 陈旧）。

#### Scenario: started 为 offline

- **WHEN** 全文齐后开始质检
- **THEN** 记录 `qc.check.started` 且 `mode=offline`

### Requirement: evidence_text 脱敏

`evidence_text` 在落库、HTTP、事件前 SHALL 经 `common.security.mask`。落库/返回的证据 MUST NOT 含完整手机号或身份证号。逾期金额 MUST NOT 进入质检 LLM。

#### Scenario: 证据不落完整手机号

- **WHEN** hit 证据原文含完整手机号
- **THEN** 落库与 API 证据已脱敏，不含完整号码

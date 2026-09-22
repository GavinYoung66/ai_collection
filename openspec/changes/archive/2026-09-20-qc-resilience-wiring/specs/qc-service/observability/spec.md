## MODIFIED Requirements

### Requirement: 服务根 GET /health 三字段

`GET /health` SHALL 挂载于 `http://qc-service:8003/health`，MUST NOT 注册在 `/api/v1` 下。SHALL 无鉴权，MUST NOT 使用 A200x 业务码。响应 SHALL 使用统一信封 `{"code","message","data","trace_id"}`，业务成功 `code==="0"`。`data` SHALL 含 `ac_version`、`llm_degraded`、`rag_connected`。API MUST NOT 新造状态键。

`llm_degraded` SHALL 为 true 当且仅当当前配置链上每一个模型均熔断 open：配置链 = `{QC_LLM_MODEL}` ∪ `QC_LLM_FALLBACK_CHAIN`（空链则仅主模型）。仅主模型 open、备用模型仍可用时，`llm_degraded` MUST 为 false（仍可发出 `qc.circuit_breaker.open`）。整链已跳过 LLM（全部超时/熔断/`LLMRateLimitError`）时 `llm_degraded` SHALL 为 true。

任一带依赖失败（`llm_degraded=true` 或 `rag_connected=false` 或词库探活无法得到有效 `ac_version`）时 `data.status` SHALL 为 `degraded`；否则为 `ok` 或 `healthy`。`degraded` SHALL 只绑定上述已有字段。

#### Scenario: 依赖成功则为健康

- **WHEN** AC 版本可用、配置链上并非全部 LLM 熔断打开、rag 词库可达
- **THEN** `data.status` 为 `ok` 或 `healthy`，且三字段存在

#### Scenario: degraded 绑定已有字段

- **WHEN** `llm_degraded=true` 或 `rag_connected=false`
- **THEN** `data.status=degraded`，不增加新键

#### Scenario: health 不在 /api/v1

- **WHEN** 客户端调用 `GET /api/v1/health`
- **THEN** 该路径不是探活入口；无鉴权探活的权威路径是服务根 `GET /health`

#### Scenario: 仅主模型熔断时 health 不标 llm_degraded

- **WHEN** `qc:llm_degraded:{primary}` open 且备用模型熔断器 closed、备用仍可调用
- **THEN** `data.llm_degraded` 为 false；`data.status` 不因仅主模型熔断而单独变为 `degraded`

#### Scenario: 整链熔断时 llm_degraded 为 true

- **WHEN** 配置链上每个模型的 `qc:llm_degraded:{model}` 均为 open
- **THEN** `data.llm_degraded` 为 true，且 `data.status=degraded`

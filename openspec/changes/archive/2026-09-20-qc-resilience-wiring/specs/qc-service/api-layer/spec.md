## ADDED Requirements

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

## MODIFIED Requirements

### Requirement: POST replay/raw

`POST /api/v1/qc/replay/raw` SHALL 接受扁平 QCSession。`X-Idempotency-Key` SHALL 必填。限流 SHALL 为 10/s（委托共享固定窗口零件，见本变更 ADDED「raw 限流委托共享固定窗口零件」）。缺可选元数据 SHALL skip 依赖规则，不是 A2002。`call_ended≠true` / 空 turns / 非法 speaker / 缺 session_id 或 case_id SHALL 返回 A2002 HTTP 422。该 session_id 无 `call_records` SHALL 返回 A2003 且不插表。

#### Scenario: raw 无通话主数据

- **WHEN** raw replay 的 session_id 不在 `call_records`
- **THEN** HTTP 200 `code=A2003`，文案要求先有通话主数据，且不插 `qc_records`

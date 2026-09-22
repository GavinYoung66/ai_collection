## Why

P0 公共韧性零件（`common/resilience/*` + `invoke_llm` + `call_tool`）已落地，complaint-service 已接线；qc-service 是三个深做服务里唯一仍用手写 Redis INCR 限流、全局 `qc:llm_degraded` 熔断、无离线并发闸的。需要把 QC 限流/LLM/并发接到同一套零件上，按模型分片熔断，同时锁住「检索失败回退关键词仍出分、LLM 挂了仍出 AC+规则分」的领域降级语义。

## What Changes

- **限流统一**：`app/routers/check.py` 的 `raw_rate_limit` 改调 `common.resilience.rate_limit.allow(key=f"qc:ratelimit:raw:{user}", limit=10, window=1, context=...)`；超限仍抛 `BizException(ErrorCode.B0006)`。**BREAKING（有意对齐 P0）**：现实现 Redis 异常 fail-close 抛 B0006；本变更改为零件约定的 Redis 挂静默放行。`context` 至少带 `trace_id`
- **LLM 统一入口**：`llm_reviewer.py` 改为 `invoke_llm(..., fallback_chain=..., timeout=Timeout(total=QC_LLM_TIMEOUT), transport=QC json_object 适配器)`；删除外层 `call_with_retry` 与私有 `QcLlmCircuit`。熔断 key `qc:llm_degraded:{model}`。超时/熔断/`LLMRateLimitError` 均跳过 LLM，HTTP 200 + A2011 + AC+规则分，不是 500
- **离线并发闸**：P0 `Semaphore` 计数 + 本服务 `acquire_wait` 排队；`QC_MAX_INFLIGHT` 默认 3；`QC_INFLIGHT_WAIT_TIMEOUT` 默认 0（无限等）；acquire 后必须 `try/finally: release()`
- **RAG 检索保持降级**：`rag_client.py` HTTP 保留；失败回退关键词仍出分；熔断 key `qc:mcp_degraded:rag_kb`。不改 `call_tool`
- **hooks 适配**：`hooks.set_publisher(适配器)`，shutdown `set_publisher(None)`。事件名 `qc.rate_limit.hit` / `qc.circuit_breaker.open` / `qc.degrade.triggered`。不进六频道 Redis。熔断告警仍 `system.alert.raised`（`event=qc:llm_degraded`，A2011）
- **health**：`llm_degraded=true` 当且仅当配置链（`QC_LLM_MODEL` + `QC_LLM_FALLBACK_CHAIN`）全部熔断 open。仅 primary open、backup 可用 → `false`

### 不做（Non-Goals）

- 不改 QC 引擎规则（`rule_engine` / `ac_filter` / `scoring` / MergeHits）
- 不下沉 QC 私有降级策略到 common
- 不把检索失败改成整单失败或 `PARTIAL`
- 不做独立 MCP 网关（P3）
- 不做 Redis Stream 队列（P5）
- 不改 M1「恰好六个 Redis 频道」白名单
- **不迁 `dp_client.py` 的 `call_with_retry`**（DP HTTP 本变更不接线）
- 不改 common 的 LLM 模型维限流阈值（60/min）
- 不等待超时变成 500，也不在满闸时超 `max_inflight` 硬跑

## Capabilities

### New Capabilities

- `qc-service/offline-qc`: 离线开检并发闸（Semaphore + 等槽），`max_inflight` 默认 3，`QC_INFLIGHT_WAIT_TIMEOUT` 默认 0

### Modified Capabilities

- `qc-service/api-layer`: raw 限流改调共享零件，10/s、用户维不变；Redis 挂放行；`allow` 传 `context.trace_id`
- `qc-service/llm-review`: `invoke_llm` + `fallback_chain` + json_object transport；禁止外层再 retry；熔断按模型分片；`LLMRateLimitError`→A2011 仍出分
- `qc-service/kb-search`: RAG HTTP + `qc:mcp_degraded:rag_kb`；失败回退关键词仍出分（不新建 `qc-service/integrations`）
- `qc-service/events`: hooks 适配器 + shutdown 注销；`qc.*` 不进六频道；熔断告警仍 `system.alert.raised`
- `qc-service/data-layer`: Redis 熔断键改为 `qc:llm_degraded:{model}` TTL 60s，废弃全局 `qc:llm_degraded`
- `qc-service/observability`: health `llm_degraded` = 配置链全部 open（不是任一模型）

## Impact

- **修改代码**：`app/routers/check.py`、`app/services/llm_reviewer.py`、`app/services/llm_circuit.py`（删除）、`app/services/offline_qc_service.py`、`app/integrations/rag_client.py`、`app/repositories/job_store.py`、`app/api/health.py`、`app/main.py`、`app/core/config.py`。**不改** `app/integrations/dp_client.py`
- **依赖**：`platform-resilience-primitives` 可 import；启动 `set_default_service("qc")`；注入 QC transport（`response_format=json_object`）
- **API 契约**：raw 超限 B0006；LLM 超时/熔断/模型维限流均 HTTP 200 + `code=A2011` + AC+规则分；检索失败不 500、不 PARTIAL
- **配置**：`QC_MAX_INFLIGHT` 默认 3、`QC_LLM_FALLBACK_CHAIN` 默认 `["qwen-turbo"]`、`QC_INFLIGHT_WAIT_TIMEOUT` 默认 0；`QC_LLM_MODEL`/`QC_LLM_TIMEOUT=5s` 不变
- **观测**：health 整链口径；全局键废弃；hooks shutdown 防测试泄漏
- **校验**：`openspec validate qc-resilience-wiring --strict`

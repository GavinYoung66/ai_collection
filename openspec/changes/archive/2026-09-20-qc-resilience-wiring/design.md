## Context

P0 零件已在 `common/resilience/*`、`common.llm.reliability.invoke_llm` 落地；complaint-service（P1）已接线。qc-service 现状与约束见 proposal.md Why，此处不重复动机。

关键现网差异：

- `raw_rate_limit` 手写 INCR；Redis 异常 **fail-close 抛 B0006**（本变更对齐零件静默放行）
- `LLMReviewer` 外层 `call_with_retry` + `QcLlmCircuit` 全局键 `qc:llm_degraded`；health/job_store 读同一键
- 现网 transport 带 `response_format=json_object`；`invoke_llm` 默认 transport 没有该字段
- `QcEventPublisher` 锁死 6 个 Redis 频道
- P0 `Semaphore.acquire()` 满即 False，不排队
- `rag_client` / `dp_client` 都用 `call_with_retry`；本变更只动 RAG，DP 不迁

## Goals / Non-Goals

**Goals:**

- 限流/熔断/并发计数走 P0 零件；LLM 走 `invoke_llm` + 注入 json_object transport
- health `llm_degraded` 用配置链全部 open 口径
- hooks 适配器 + shutdown 注销；不破坏六频道
- 离线在途 ≤ `QC_MAX_INFLIGHT`，超额等槽，finally 释放

**Non-Goals:**

- 不把 P0 Semaphore 改成阻塞原语
- 不把 rag_client 改成 MCP；不迁 dp_client
- 不改检测顺序/扣分/MergeHits
- 不引入有界丢弃队列；等待超时默认 0，不因此 500

## Decisions

### D1. raw 限流：`allow(key, 10, 1, context=)`，Redis 挂放行

`await rate_limit.allow(f"qc:ratelimit:raw:{user}", limit=10, window=1, context={"trace_id": ...})`，`False` 抛 B0006。用户维 `X-User-Id` → IP → `anon`。

**备选**：保留 Redis 故障抛 B0006。——拒绝，对齐 P0 静默放行。

### D2. LLM：`invoke_llm` + 注入 json_object transport + 删除 QcLlmCircuit

启动 `set_default_service("qc")`。对照 P1 `ComplaintLLMProvider`：注入 `(model, messages, timeout_seconds) -> str` 适配器，内部走现网 `post_chat_completions(..., response_format={"type": "json_object"}, model=model, timeout=timeout_seconds)`。`timeout=Timeout(total=QC_LLM_TIMEOUT)`（5s）。禁止外层 `call_with_retry`。

整链失败、`LLMCircuitOpenError`、`LLMRateLimitError`：跳过 LLM hits，HTTP 200 + A2011 + `system.alert.raised`。不改 common 60/min 阈值。

删除 `llm_circuit.py` 与 job_store 全局 `qc:llm_degraded`。

**备选**：裸用默认 transport。——拒绝，会丢掉 json_object。

**备选**：只换 retry 保留全局 QcLlmCircuit。——拒绝，误杀备用模型。

### D3. 离线闸：P0 Semaphore + acquire_wait + finally release

满则 Condition 等待。`QC_MAX_INFLIGHT` 默认 3。`QC_INFLIGHT_WAIT_TIMEOUT` 默认 0=无限等。`acquire` 成功后 `try/finally: release()`。

**备选**：等待超时变 500。——拒绝，违反「排队不丢单」。

**备选**：改 P0 Semaphore 为阻塞。——拒绝，不倒灌 common。

### D4. RAG：HTTP + `qc:mcp_degraded:rag_kb`

失败/超时/open 返回空，既有关键词回退出分。可保留瞬时 `call_with_retry`。不改 `call_tool`。

### D5. hooks 适配器 + shutdown 注销

`hooks.set_publisher(qc_resilience_on_event)`；shutdown `set_publisher(None)`。适配器只打 structlog / 可测 emitted，不调用六频道 `_publish`。LLM 熔断仍 `publish_system_alert_raised`。

**备选**：直连 `publisher.publish`。——拒绝，白名单会拒 `qc.rate_limit.hit`。

### D6. health 整链口径

`llm_degraded=true` iff `QC_LLM_MODEL` ∪ `QC_LLM_FALLBACK_CHAIN` 全部 open。仅 primary open 且 backup 可用 → `false`（LLM 复核仍能走备用，探活不应报整服务 LLM 降级）。

**备选**：任一模型 open 即 true。——拒绝，主挂备活时误报 degraded。

### D7. dp_client 不迁

`app/integrations/dp_client.py` 继续 `call_with_retry`。本变更范围不含 DP HTTP，避免和「QC 已全部接 P0」夸大。

## Risks / Trade-offs

- **[Risk] Redis 限流从 fail-close 变放行** → 单测改断言；换可用性
- **[Risk] 外层 retry × invoke_llm 内重试** → 删除 `call_with_retry` 主路径；MODIFIED 旧「重试 3×」条
- **[Risk] 默认 transport 丢 JSON** → 强制注入 json_object 适配器
- **[Risk] LLMRateLimitError 变 500** → 与熔断同一 A2011 出分
- **[Risk] 槽位泄漏** → try/finally release，单测取消/异常
- **[Risk] 无限等待拖死 batch** → 默认 0 保持原验收；已知风险，本变更不加超时失败码
- **[Risk] health 误报** → 整链口径（D6）
- **[Risk] hooks 泄漏测试** → shutdown `set_publisher(None)`

## Migration Plan

1. 限流 + context
2. LLM 入口 + transport + 删全局键 + health 整链
3. RAG breaker
4. 离线闸 + finally
5. hooks + shutdown
6. 全量 qc-service pytest + `openspec validate qc-resilience-wiring --strict`

回滚：恢复私有 INCR / QcLlmCircuit / 全局键；限流 Redis 故障回到 fail-close。

## Open Questions

（无）

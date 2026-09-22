## Why

complaint-service 已有完整的降级三件套（degrade_flow/degrade_llm/degrade_mcp）、5 维限流、LLM 熔断、规则兜底引擎，但这些韧性能力都是服务内私有实现，无法被 qc-service / rag-service 复用。现状问题：

- rag-service 用一套独立的固定窗口限流（Redis INCR），与 complaint 口径不同；
- qc-service 几乎无限流和降级，只靠 common 的 retry/circuit_breaker；
- `common/llm/circuit_breaker.py` 当前是一个全局 pybreaker 实例（`_llm_breaker`），一个 key 会干掉全平台 LLM 调用；
- MCP 调用没有熔断器，超时和权限拒绝也走重试，无错误分级；
- 没有幂等键机制，重试可能导致重复写入；
- 没有统一的可观测钩子，降级/限流/熔断发生时没有统一计数点。

需要在 `common/` 下构建一套"韧性原语零件库"，三服务 import 复用。零件只提供原子能力，领域降级策略（如投诉转人工、QC 回退关键词仍出分）留各服务自己定。P0 只定接口+单测，不改业务语义。

## What Changes

### P0 公共下沉 — 新增零件文件

- 新增 `common/resilience/` 目录及 7 个原子零件：
  - `rate_limit.py`：固定窗口计数（Redis INCR + EXPIRE），超限返回 False；Redis 挂静默放行
  - `concurrency.py`：基于 asyncio.Semaphore 的有界并发，满返回 False（不阻塞等待）
  - `queue.py`：有界队列，满则 push 返回 False（不静默丢数据）
  - `idempotency.py`：Redis SETNX + TTL 幂等键，已存在返回 False
  - `breaker.py`：CircuitBreaker 状态机（closed→open→half-open→closed/open），熔断 key 强制带服务前缀 `{svc}:llm_degraded:{model}` / `{svc}:mcp_degraded:{server}`
  - `hooks.py`：统一事件钩子 `on_event(name, payload)`，默认打 structlog，不依赖 publisher
  - `timeout.py`：三级超时 dataclass（connect/read/total），层级关系 MCP total=30s < LLM total=120s < 投诉全局 30min
- 新增 `common/mcp/reliability.py`：`call_tool()` 统一编排，错误分级（超时不重试、权限拒绝不重试、业务可重试错误重试 3 次），按 server 熔断
- 新增错误码 A5015（Token 超限→转人审）、A5016（任务超时→转人审）、A5017（LLM 熔断→转人审/规则草稿），沿用已有 B0006（限流）；A5009/A5010/A5011 已被 `common/errors/codes.py` 占用，故续用 A5 域末尾 A5015/A5016/A5017

### P0 修改/补全已有文件

- 修改 `common/llm/reliability.py`：补全 `invoke_llm(model, messages, *, fallback_chain, timeout, breaker_key)` 统一编排入口（选 Key → 限流 → 熔断检查 → 有界重试 → fallback 换模型 → 降级），熔断 key 不传则自动用 `{svc}:llm_degraded:{model}`

### P0 唯一允许修改的业务文件

- 修改 `services/rag-service/app/services/rate_limit.py`：改为 import `common.resilience.rate_limit`，阈值 20/60/200、用户维（X-User-Id 优先回退 IP）、Redis 挂放行行为保持不变

### 不做（Non-Goals）

- 不下沉 degrade_flow / degrade_llm / degrade_mcp / fallback_engine（领域策略留各服务）
- 不把投诉 5 维限流写进 common（P1 由投诉服务用零件拼装）
- 不上 Prometheus / Grafana（P1 用 hooks 打计数即可）
- 不做第 8 个 resilience-service（公共层是 Python 库，不是微服务）
- 不做全局 `csmp:llm_degraded=1` 熔断全平台
- 不在 P0 用 Redis Stream 替换 list 队列
- 不改投诉图、QC 引擎、Nginx、独立 MCP/LLM 网关

## Capabilities

### New Capabilities

- `platform/resilience-primitives`: 公共韧性原语零件库（限流/并发/队列/幂等/熔断/钩子/超时），三服务可 import 复用，不含领域降级策略
- `platform/mcp-reliability`: MCP 统一调用可靠性（call_tool：错误分级 + 按 server 熔断 + 不重试策略）

### Modified Capabilities

- `platform/llm-reliability`: 新增 `invoke_llm` 统一编排入口契约（fallback 链 + 三级超时 + 按模型熔断 key），补强已有"服务前缀 degrade key"和"Redis 多实例权威"要求，明确禁止全局单 key 熔断
- `rag-service/hybrid-search`: 限流实现从服务内私有改为调 `common.resilience.rate_limit`，限流阈值/用户维度/Redis 挂放行行为不变

## Impact

- **新增代码**：`common/resilience/` 7 个零件文件 + `common/mcp/reliability.py` + 对应单测
- **修改代码**：`common/llm/reliability.py`（补 invoke_llm）、`services/rag-service/app/services/rate_limit.py`（迁移到 common 零件）
- **API 契约**：新增错误码 A5015/A5016/A5017，前端需看 `task_status` 字段而非仅 HTTP status code（降级成功时常 200）
- **依赖**：复用现有 redis.asyncio、asyncio.Semaphore、structlog；不引入新第三方库
- **架构约束**：零件 vs 策略分离——common 只放原子零件，领域降级策略不下沉；熔断 key 必须带服务前缀

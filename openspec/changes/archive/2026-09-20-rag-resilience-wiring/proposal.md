## Why

P0 公共韧性零件（`common/resilience/*` + `invoke_llm`）与 complaint/qc 接线已落地，rag-service 仍半接通：限流已走 `rate_limit.allow`，但 3 个 LLM provider 仍各自重试并写全局 `rag:llm:degraded`，embed/rerank/ocr/asr 仍裸 httpx + 手写 `rag:{resource}:degraded`，启动未 `set_default_service("rag")` / `hooks.set_publisher`。需要把外部调用接到同一口径，避免熔断 key 全局误杀、双层重试放大延迟、降级冒 500。

## What Changes

- **LLM 统一入口**：`llm_service.py` 改调 `common.llm.reliability.invoke_llm`；3 个 provider 只保留 HTTP transport + JSON 解析，删除自写 3 次重试与 `RedisDegradeTracker`。`factory.py` 按 `LLM_PROVIDER` 产出 `model` + `fallback_chain` + transport，不再实例化带重试的完整 provider
- **fallback 链**：`LLM_PROVIDER=dashscope` → 主 `qwen-plus`、备用 `qwen-turbo`；`deepseek` → 单元素 `deepseek-chat`（无 fallback）；`local` → 单元素 `LLM_MODEL`。超时 `Timeout(total=LLM_TIMEOUT)`（沿用现网默认 10s，不改阈值）
- **整链失败不 500**：`answer=null` + `degraded=true`，检索结果（含 BM25/关键词召回）仍返回，HTTP 200
- **embed/rerank/ocr/asr 外包 CircuitBreaker**：直接 HTTP，**不用** `call_tool`。独立 key `rag:mcp_degraded:{embed|rerank|ocr|asr}`，`fail_max=3`、`reset_timeout=60s`。open 时：embed 空向量→BM25；rerank 跳过用原始分；ocr/asr 标记失败且不 500、不卡死入库
- **基础设施**：启动 `set_default_service("rag")` + `hooks.set_publisher(...)`（`rag.rate_limit.hit` / `rag.circuit_breaker.open` / `rag.degrade.triggered` 走 structlog，不进投诉六频道）；shutdown `hooks.set_publisher(None)`。限流 key 已是 `rag:ratelimit:{bucket}:{user}`，前缀不改
- **删死代码**：去掉 `RedisDegradeTracker`、provider 私有 retry、手写 `rag:llm:degraded` / `rag:embed:degraded` / `rag:rerank:degraded` / `rag:ocr:degraded` / `rag:asr:degraded`；`asr_client.py` 重复写 ASR 熔断键一并删除。grep 无残留

### 不做（Non-Goals）

- 不新增零件，不新增 MCP 协议层，embed/rerank/ocr/asr **禁止**改走 `call_tool`
- 不加 Semaphore + BoundedQueue（查询为主，无批量削峰）
- 不加两级 deadline（同步短请求，无排队/执行分离）
- 不加 idempotency（查询无副作用）
- 不改 `search.py` 在 `answer=true` 时把请求超时放宽为 `LLM_TIMEOUT + 10s`
- 不迁 Query 改写（`query_rewrite_service.py` / `rag:rewrite:degraded` 仍私有，跟进另案）
- 不迁数据平台被调路径；rag 无 `dp_client.py`，本变更不引入
- 不改 LLM 回答 Prompt / 脱敏 / 双校验 / 开关门控语义
- 不改 common 模型维限流 60/min、不改 `LLM_TIMEOUT` 默认值

## Capabilities

### New Capabilities

- `rag-service/observability`: 启动注入 hooks publisher，韧性事件 `rag.*` 走 structlog；shutdown 注销防测试泄漏
- `rag-service/data-layer`: 冻结 rag Redis 限流/熔断 key 清单；废弃全局 `rag:llm:degraded` 与 `rag:{embed|rerank|ocr|asr}:degraded`

### Modified Capabilities

- `rag-service/llm-answer`: LLM 回答走 `invoke_llm` + fallback_chain；熔断按模型 `rag:llm_degraded:{model}`；整链失败空 answer + 检索仍出结果，不 500
- `rag-service/hybrid-search`: embed/rerank 外包 `CircuitBreaker`（key `rag:mcp_degraded:embed|rerank`）；open 时 BM25 / 原始分排序；限流 key 前缀保持 `rag:`
- `rag-service/multimodal-ingest`: ocr/asr 外包同一零件（key `rag:mcp_degraded:ocr|asr`）；open 时标记解析失败且不阻塞/不 500；废弃 `rag:ocr:degraded` / `rag:asr:degraded`

## Impact

- **修改代码**：`app/services/llm_service.py`、`llm_providers/{dashscope,deepseek,local_openai,factory}.py`、`vectorstore/{embed,rerank}_client.py`、`services/ocr_client.py`、`asr_providers/dashscope_provider.py`、`asr_client.py`（删重复熔断）、`app/main.py`、`services/rate_limit.py`（核对前缀 + 透传 `trace_id`）。不改 `api/search.py` 的 LLM 超时放宽、不改 `query_rewrite_service.py`
- **依赖**：`platform-resilience-primitives` 可 import；启动 `set_default_service("rag")` 并把 rag Redis 注入 common（限流已注入，供 invoke_llm 共用）
- **API 契约**：检索/入库 HTTP 成功码不变；LLM/embed/rerank/ocr/asr 熔断降级不 500。OCR/ASR 电路 open 视为解析失败（A3001 或 `KB_OCR_ASR_DEGRADED_KEEP_TEXT` 占位），不得当成空白识别成功（A3002）
- **配置**：fallback 由 `LLM_PROVIDER` 派生，不强制新 env；`LLM_TIMEOUT` / `LLM_MAX_CONCURRENCY` 不变
- **观测**：hooks 事件 `rag.*`；熔断 key 分模型/分资源；无全局 `rag:llm:degraded`
- **校验**：`openspec validate rag-resilience-wiring --strict`

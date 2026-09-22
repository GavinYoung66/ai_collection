## Context

动机见 proposal.md Why。现网约束：

- 限流已委托 `rate_limit.allow`，key `rag:ratelimit:{bucket}:{user}`；首次限流才 `configure_redis`
- `LLMService` + 3 个 Provider 各有 `call_with_retry` / `RedisDegradeTracker`，全局键 `rag:llm:degraded`
- embed/rerank/ocr/asr（及 `ASRClient` 重复一份）手写失败计数 + `rag:{resource}:degraded`
- `main.py` 仍用 `@app.on_event("startup"|"shutdown")`，未 `set_default_service` / `hooks.set_publisher`
- `LLM_TIMEOUT` 平台默认 **10s**（不是 5s）；`LLM_MAX_CONCURRENCY` 信号量已存在
- DashScope 回答现走原生 text-generation；`invoke_llm` 默认 transport 是 compatible-mode chat completions
- Query 改写仍私有 `rag:rewrite:degraded`，本设计不迁

## Goals / Non-Goals

**Goals:**

- LLM 只经 `invoke_llm` 做限流→熔断→重试→fallback；provider 变 transport
- 四个 HTTP 外包独立 `CircuitBreaker` + `Timeout`，key 用 `rag:mcp_degraded:{resource}`
- 启动注入服务名与 hooks；shutdown 注销
- 删全局 degraded 键与双层 retry；每个外部调用点有熔断 open 降级单测

**Non-Goals（设计层）：**

- 不把 FastAPI `on_event` 整段重写成 lifespan（只在现有 startup/shutdown 加接线）
- 不把 Query 改写并进本次 factory/invoke_llm
- 不改 `LLM_TIMEOUT` 默认 10s 去迎合「5s」口述
- 不把 OCR/ASR 熔断成功路径伪装成 A3002 空白识别

## Decisions

### D1. LLM：`invoke_llm` + 注入 provider transport，删除 RedisDegradeTracker

`LLMService.generate_answer` 在脱敏/前置拦截之后调用 `invoke_llm`。`LLMFactory` 返回 `(model, fallback_chain, transport)`：

| `LLM_PROVIDER` | `model` | `fallback_chain` |
|---|---|---|
| dashscope | `LLM_MODEL`（默认 qwen-plus） | `["qwen-turbo"]`（若主模型已是 turbo 则为空） |
| deepseek | `deepseek-chat` | `[]` |
| local | `LLM_MODEL` | `[]` |

`timeout=Timeout(total=float(settings.LLM_TIMEOUT))`。transport 签名 `(model, messages, timeout_seconds) -> str`，内部只发 HTTP + 解析，**禁止** retry/熔断。整链 `content is None` / `LLMCircuitOpenError` / `LLMRateLimitError` → `answer=null` + `degraded=true`，检索 results 原样返回。

保留 `LLM_MAX_CONCURRENCY` 信号量包住 `invoke_llm`（超限等待不降级）。删除 llm_service 与 3 个 provider 的 `RedisDegradeTracker` / `call_with_retry`。

**备选**：裸用默认 compatible-mode transport。——拒绝，会丢掉现网 DashScope 原生 endpoint / DeepSeek / local base_url。

**备选**：只换 retry 保留全局 `rag:llm:degraded`。——拒绝，误杀备用模型。

### D2. `LLM_TIMEOUT` 保持 10s

口述「5s」与代码/主 spec 默认 10s 不符。本变更 `Timeout(total=LLM_TIMEOUT)` 读现网值，不改默认。`search.py` 的 `LLM_TIMEOUT + 10s` 请求超时放宽不动。

**备选**：改默认 5s 对齐 QC。——拒绝，无验收要求改 SLA，且会收紧现网回答超时。

### D3. 四个 HTTP：独立 BreakerRegistry，不用 `call_tool`

新建 rag 模块级 `BreakerRegistry(fail_max=3, reset_timeout=60)`（或每客户端一个 `CircuitBreaker`），key：

- `rag:mcp_degraded:embed`
- `rag:mcp_degraded:rerank`
- `rag:mcp_degraded:ocr`
- `rag:mcp_degraded:asr`

HTTP 外包 `Timeout(total=既有 EMBED/RERANK/OCR/ASR_TIMEOUT)`。`before_call() is False` 走降级；成功 `record_success`，失败 `record_failure`。删除手写失败计数与 SETEX。

用 `mcp_degraded` 形态是为通过零件 `validate_breaker_key`（与 qc `qc:mcp_degraded:rag_kb` 相同约定），**不**表示这些调用是 MCP，也 **禁止** `call_tool`。

删除 embed/rerank 客户端内 3 次指数退避：瞬时重试改由调用方既有降级语义承担，避免与熔断计数双计。OCR/ASR 同样去掉自写 3 次退避（熔断 fail_max=3 已覆盖连续失败）。

**备选**：走 `call_tool`。——拒绝，对端不是 MCP server。

**备选**：沿用 LLM 的 fail_max=5。——拒绝，辅助调用更快降级（提案 fail_max=3）。

### D4. 熔断 open 的返回契约

- **embed**：open 或失败返回空向量；`search_service` 走 ES-only/BM25，`degraded=true`。`batch_embed` 在 texts 非空却得到空列表时，入库按既有 Embedding 失败（A3003 或 `KB_EMBED_DEGRADED_KEEP_TEXT`），不得把空向量写入 Milvus。
- **rerank**：open 或失败返回空/抛给 search 已有 catch；跳过精排，用 RRF 分排序。
- **ocr/asr**：open 时 **不** 返回「识别成功的空字符串」。短路 HTTP 并抛/返回失败，入库映射 A3001 或占位降级。空白图成功识别仍是 A3002。内嵌图单张失败仍 skip。

`ASRClient` 删除 `_DEGRADED_KEY` 与失败计数；熔断只留在 DashScope provider。Whisper 不写 Redis 熔断键。

**备选**：OCR open 返回 `""` 当成功。——拒绝，会误走 A3002。

### D5. 启动接线挂现有 startup/shutdown

在现有 `startup()` 开头：`set_default_service("rag")`、`hooks.set_publisher(rag_on_event)`、把 rag Redis `configure_redis`（与限流共用 DB）。`shutdown()` 末尾 `hooks.set_publisher(None)`。适配器只 structlog / 可测 emitted，**不**调投诉 EventPublisher。限流 `allow(..., context={"trace_id": get_trace_id()})`。

**备选**：整文件改 `@asynccontextmanager lifespan`。——拒绝，startup 已有 Milvus/ES/敏感词/backfill/sweep，无行为收益。

### D6. Query 改写本变更不迁

`query_rewrite_service.py` 与 `rag:rewrite:degraded` 保持。grep 残留允许这一处；提案 Non-Goals 已声明。

**备选**：改写也走 `invoke_llm`。——拒绝，扩大范围且改写超时/置信度语义独立。

## Risks / Trade-offs

- **[Risk] 外层 retry × invoke_llm 内重试** → 删除 provider `call_with_retry` 与 embed/ocr 自写 3 次循环
- **[Risk] 默认 transport 改 endpoint** → 强制注入现网 transport（D1）
- **[Risk] LLM_TIMEOUT 口述 5s vs 代码 10s** → D2 保持 10s
- **[Risk] OCR 空串当成功** → D4 失败标记，单测区分 A3001 vs A3002
- **[Risk] ingest 把空 embedding 写入 Milvus** → batch_embed 空结果走失败路径
- **[Risk] 进程内 CircuitBreaker 多实例不同步** → 与 P0 相同；Redis 降级标志仅 LLM `mark_degraded_flag`；HTTP 四路本变更不另写全局 SETEX
- **[Risk] hooks 泄漏测试** → shutdown `set_publisher(None)`
- **[Risk] `is_degraded()` 探测 Redis 500** → 改读 CircuitBreaker 状态；探测异常仍按未熔断
- **[Risk] 旧单测断言 `rag:llm:degraded` / SETEX** → 改为 `{svc}:llm_degraded:{model}` 与 breaker open 降级

## Migration Plan

1. 启动接线：`set_default_service` + hooks + `configure_redis`
2. LLM factory/transport + `invoke_llm`；删 tracker/retry；更新 `test_llm_answer.py`
3. embed/rerank breaker；更新检索降级单测
4. ocr/asr breaker；删 `ASRClient` 重复键
5. grep 无 `RedisDegradeTracker` / `rag:llm:degraded` / `rag:embed:degraded` 等（允许 `rag:rewrite:degraded`）
6. rag-service 全量 pytest + `openspec validate rag-resilience-wiring --strict`

回滚：恢复 provider retry + 全局 SETEX 键；去掉 startup hooks。

## Open Questions

（无）

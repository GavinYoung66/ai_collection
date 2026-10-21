## Purpose

LLM 编排层 per-call 用量可观测能力：在 `invoke_llm` 编排路径上落地结构化日志与 Prometheus 指标，覆盖调用量、token 吞吐、延迟、fallback、限流、熔断六个维度，让"看清用量再调阈值"成为可执行操作而非口号。

## ADDED Requirements

### Requirement: per-call 结构化日志

`common/llm/reliability.py` SHALL 在 `invoke_llm` 全路径的三个埋点位置写 structlog info 日志，覆盖每次编排处理的终态：

- **transport 调用终态（`ok` / `timeout` / `error`）**：在 `_call_model` 内每次真实 transport 调用（含 primary 与每次 fallback 候选）结束后写日志；
- **熔断拒绝（`breaker_open`）**：在 `_call_model` 入口 `breaker.before_call()` 返回 False 抛 `LLMCircuitOpenError` 处写日志（此时 transport 未被调用）；
- **限流拒绝（`rate_limited`）**：在 `invoke_llm` 抛 `LLMRateLimitError` 处写日志（此时未进入 `_call_model`）。

日志字段 SHALL 包含：`event="llm_call"`、`svc`、`model`、`fallback_from`（primary 时为 `None`）、`breaker_key`、`status`（取值 `ok|rate_limited|breaker_open|timeout|error`）、`latency_ms`、`prompt_tokens`、`completion_tokens`、`total_tokens`、`token_source`（取值 `usage|missing`）。`rate_limited` 与 `breaker_open` 场景的 token 字段 SHALL 为 0、`token_source="missing"`、`latency_ms=0`。日志 SHALL NOT 修改 `invoke_llm` 与 `_call_model` 的对外签名、返回值结构、异常类型。日志与后续指标的 `svc` 字段/标签 SHALL 取自 `resolve_service_name()` 的服务缩写（complaint-service→`csmp`、rag-service→`rag`、qc-service→`qc`），与熔断/限流 key 前缀同源。

#### Scenario: 成功调用产生完整日志

- **WHEN** complaint-service 在 `USE_MOCK_LLM=false` 模式下完成一次真实 LLM 调用
- **THEN** stdout 日志出现 `event=llm_call`，且字段 `svc="csmp"`、`status="ok"`、`latency_ms>0`、`token_source="usage"`、`prompt_tokens>0`

#### Scenario: 限流命中日志

- **WHEN** `LLM_RATE_LIMIT=1` 且已发生一次调用后再次发起调用
- **THEN** 日志出现 `event=llm_call` 且 `status="rate_limited"`、`model` 为被限流模型

#### Scenario: 熔断打开日志

- **WHEN** 某模型 breaker 处于打开状态时再次调用该模型
- **THEN** 日志出现 `event=llm_call` 且 `status="breaker_open"`，不实际发起 transport 调用

### Requirement: LLM Prometheus 指标

`common/observability/llm_metrics.py` SHALL 定义并由 `mount_metrics` 一并注册以下指标：

- `llm_calls_total{svc,model,status}` Counter——**仅计真实 transport 调用**（`status ∈ {ok, timeout, error}`）；`rate_limited` 与 `breaker_open` SHALL NOT 计入本指标，由独立指标承载；
- `llm_tokens_total{svc,model,kind}` Counter，`kind ∈ {prompt,completion}`——按 usage 计数，`token_source=missing` 时 +0；
- `llm_call_latency_seconds{svc,model}` Histogram，buckets=`[0.1, 0.5, 1, 2, 5, 10, 30, 60]`——仅计真实 transport 调用；
- `llm_fallback_total{svc,from_model,to_model}` Counter——埋点在 `invoke_llm` 的 fallback 循环 `continue` 之前，计数 `(chain[i] → chain[i+1])`；
- `llm_rate_limited_total{svc,model}` Counter——埋点在 `invoke_llm` 抛 `LLMRateLimitError` 之前；
- `llm_breaker_open_total{svc,model}` Counter——埋点在 `_call_model` 入口 `breaker.before_call()` 返回 False 抛 `LLMCircuitOpenError` 之前。

以上指标的 `svc` 标签同「per-call 结构化日志」口径（`resolve_service_name()` 缩写，如 complaint-service→`csmp`）。

#### Scenario: 成功调用指标增长

- **WHEN** complaint-service 完成一次真实 LLM 调用
- **THEN** `llm_calls_total{svc="csmp",status="ok"}` +1，`llm_tokens_total{svc="csmp",kind="prompt"}` 增量 > 0，`llm_call_latency_seconds_count` +1

#### Scenario: fallback 链计数

- **WHEN** primary 模型 A 失败，fallback 到模型 B 成功
- **THEN** `llm_fallback_total{svc,from_model="A",to_model="B"}` +1，且 A 与 B 各产生一条 `llm_calls_total`（A 为 `status="error"`，B 为 `status="ok"`）

### Requirement: token usage ContextVar 透传

`common/llm/transport.py` SHALL 新增 `_llm_last_usage: ContextVar[dict | None]`。`post_chat_completions` SHALL 在成功拿到 dict body 且 body 含 `usage` 字段时把 `usage` 写入 ContextVar；body 不含 `usage` 时写入 `None`。`invoke_llm` SHALL 在每次调用进入时 reset ContextVar（防止上一次调用残留污染下一次），并在 `finally` 块中读取并清零（覆盖成功与失败/异常路径）：读到 dict → `token_source="usage"` 并按值计数；读到 `None` → `token_source="missing"`、token 计数为 0。

#### Scenario: 默认通道自动覆盖

- **WHEN** 任何 transport（包括 `_default_llm_transport` 与 `RealLLMTransport`）经 `post_chat_completions` 拿到含 `usage` 的响应
- **THEN** 当次 `invoke_llm` 日志的 `token_source="usage"`，`llm_tokens_total` 按真实值计数

#### Scenario: 不含 usage 的兜底

- **WHEN** transport 返回的 body 不含 `usage` 字段（如某些 mock / 老旧通道）
- **THEN** 当次日志 `token_source="missing"`、`prompt_tokens=0`、`completion_tokens=0`，`llm_calls_total` 仍 +1

#### Scenario: ContextVar 跨调用隔离

- **WHEN** 连续两次 `invoke_llm` 调用，第一次拿到 usage、第二次 transport 未调 `post_chat_completions`（如纯 mock）
- **THEN** 第二次调用日志 `token_source="missing"`，不复用第一次的 usage 值

### Requirement: 既有行为兼容

本 capability 的所有埋点 SHALL NOT 修改 `invoke_llm` / `_call_model` / `post_chat_completions` 的对外签名、返回值结构、异常类型。`complaint-service/app/llm/real_transport.py` 的限流与重试逻辑 SHALL 保持不变（属 P1#13 另开 change 的范围）。rag-service 的 `LLMFactory` 与 qc-service 的 `_as_transport` SHALL NOT 在本 change 改造——若其底层不调 `post_chat_completions`，本期对应调用 `token_source="missing"`。

#### Scenario: 既有测试不破坏

- **WHEN** 运行 `common/tests/test_llm_reliability.py`、`services/complaint-service/tests/unit/test_llm_real_path.py`、`services/complaint-service/tests/unit/test_llm_provider_fallback.py`
- **THEN** 除为指标重命名做的最小更新外，所有既有断言通过，无新增失败

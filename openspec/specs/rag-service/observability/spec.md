# RAG Observability Specification

## Purpose

为 rag-service 接入平台韧性钩子：启动注入 publisher，把限流命中、熔断打开、降级触发记为 `rag.*` 结构化事件，shutdown 注销以免测试与进程间泄漏。

## Requirements

### Requirement: 韧性 hooks 事件带 rag 前缀且不进投诉六频道

rag-service 启动 MUST 调用 `set_default_service("rag")`，使熔断/限流 key 带 `rag:` 前缀。启动 SHALL 调用 hooks publisher 注入本服务适配器。shutdown MUST 将 publisher 置为 None，避免测试间泄漏。韧性事件名 SHALL 为 `rag.rate_limit.hit`、`rag.circuit_breaker.open`、`rag.degrade.triggered`（及零件发出的同前缀变体）。有请求上下文时 payload SHALL 携带 `trace_id`，并含 `reason` / `key` 等零件字段。适配器 MUST 将事件落到 structlog（或等价可测记录），MUST NOT 将这些事件 PUBLISH 到投诉服务的六个 Redis 业务频道。适配器或下游失败 MUST NOT 抛异常阻断检索/入库主流程。

#### Scenario: 限流命中发出 rag.rate_limit.hit

- **WHEN** 搜索或上传限流超限
- **THEN** 钩子发出 `rag.rate_limit.hit`，payload 含 `trace_id`（若请求上下文可得），且投诉六频道订阅方收不到该事件

#### Scenario: 熔断打开发出 rag.circuit_breaker.open

- **WHEN** embed/rerank/ocr/asr 或某 LLM 模型熔断转为 open
- **THEN** 钩子发出 `rag.circuit_breaker.open`，payload 含对应 `rag:llm_degraded:{model}` 或 `rag:mcp_degraded:{resource}` key

#### Scenario: shutdown 注销 hooks publisher

- **WHEN** 服务进程关闭或测试 teardown 调用 shutdown
- **THEN** hooks publisher 被置为 None，后续事件回退默认 structlog，不阻断、不泄漏到下一用例

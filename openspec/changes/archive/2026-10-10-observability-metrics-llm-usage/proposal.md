## Why

README「8.2 待办进度条 🔴 P0」第 5、6 两条硬门槛未落地：8 个业务服务中仅 mcp-gateway 暴露 `/metrics`，其余 7 个完全无指标；仓库无 Prometheus 抓取配置、无 Grafana；`invoke_llm` 编排器只有告警级日志，**无 per-call 结构化用量日志与指标**，LLM token 消耗、限流命中、fallback 比率、熔断打开次数全部不可见。父 change `infra-production-readiness` 已交付 CI/sops/备份/CD 并明确把"Prometheus / Grafana / 指标采集 / LLM 用量观测"留给"下一个 change"，本次即承接，为上生产前最后一道可观测性门槛。

## What Changes

- **新增 common/observability 模块**：纯 ASGI `MetricsMiddleware`（沿用 mcp-gateway 现有实现模式，避免 `BaseHTTPMiddleware` 缓冲破坏 SSE）+ `mount_metrics(app, service_name)` 帮助函数；统一指标 `http_requests_total{svc,method,path,status}` / `http_request_duration_seconds{svc,method,path}` / `http_requests_in_flight{svc}`，path 标签走白名单模板防高基数。
- **8 个业务服务全部暴露 `/metrics`**：complaint / rag / qc / data-platform / mcp-gateway 统一改用 common `mount_metrics`；call / case / review 三脚手架最小挂载。**BREAKING**：mcp-gateway 指标名由 `mcp_gateway_requests_total` / `mcp_gateway_active_connections` 废弃，迁移到 `http_requests_total{svc="mcp-gateway",...}` / `http_requests_in_flight{svc="mcp-gateway"}`；mcp-gateway 的 `_protected_kind` 把 `/metrics` 移出鉴权保护类别（内部系统短路放行）。
- **新增 Prometheus + Grafana 到主 docker-compose.yml**：`prom/prometheus:v2.54.0`（端口 9090）+ `grafana/grafana:11.2.0`（端口 3000），抓取目标经 `host.docker.internal:{宿主机端口}` 跨 compose 触达 `docker-compose.dev.yml` 单实例全栈的 8 个服务；新增 `deploy/prometheus/prometheus.yml`（含自抓 job）与 `deploy/grafana/provisioning/{datasources,dashboards}` 内置 dashboard「AI-Collection Overview」。
- **invoke_llm 全链路 LLM 用量埋点**：`invoke_llm` 全路径三埋点写 structlog `event=llm_call`（① `_call_model` 内 transport 调用终态 ok/timeout/error；② `_call_model` 入口熔断拒绝 breaker_open；③ `invoke_llm` 限流拒绝 rate_limited），字段含 svc/model/fallback_from/breaker_key/status/latency_ms/prompt_tokens/completion_tokens/total_tokens/token_source；并打 Prometheus 指标 `llm_calls_total`（仅计真实 transport 调用）/ `llm_tokens_total{kind}` / `llm_call_latency_seconds` / `llm_fallback_total{from_model,to_model}` / `llm_rate_limited_total` / `llm_breaker_open_total`（rate_limited/breaker_open 由独立指标承载，不计入 `llm_calls_total`）。
- **token usage 透传**：在 `common/llm/transport.py` 的 `post_chat_completions` 成功返回 body 且含 `usage` 字段时写入 `_llm_last_usage: ContextVar[dict | None]`；`invoke_llm` 进入时 reset、`finally` 块中读取并清零（覆盖成功与失败/异常路径）——**一处改动覆盖所有调 `post_chat_completions` 的 transport**（默认通道 + complaint 的 RealLLMTransport），不改任何 transport 签名。
- **README 状态翻转**：8.2 表第 5、6 两行由 🔴 ❌ 改为 🟢 ✅（apply 完成后）。

## Capabilities

### New Capabilities

- `platform/observability`: 平台级 HTTP 指标采集与 Prometheus/Grafana 接入能力。纯 ASGI 中间件、`/metrics` 端点规范、path 白名单防高基数、Prometheus 抓取拓扑（host.docker.internal 跨 compose）、Grafana 内置 dashboard、端口分配约定。
- `platform/llm-usage-observability`: LLM 编排层 per-call 用量可观测能力。`invoke_llm` 结构化日志规范、六类 Prometheus 指标定义与埋点位置、`post_chat_completions` 层 usage ContextVar 透传机制、token_source=usage|missing 口径。

### Modified Capabilities

（无既有 capability 的需求发生变更；mcp-gateway 的 `/metrics` 指标名迁移属于本 change 新增的 `platform/observability` capability 内部的实现约束，不改 `platform/mcp-gateway` spec 的需求级行为。）

## Impact

- **新增文件**：
  - `common/observability/__init__.py`、`common/observability/metrics.py`、`common/observability/llm_metrics.py`
  - `common/tests/test_observability_metrics.py`、`common/tests/test_llm_metrics.py`、`common/tests/test_llm_usage_observability.py`（per-call 用量埋点：日志字段 + 六类指标 + ContextVar 隔离）
  - `common/tests/_metrics_regression.py`（8 服务 `/metrics` 回归的共享断言 helper，唯一事实源）
  - 8 个服务各一个 `/metrics` 回归测试：`services/<svc>/tests/test_metrics_<svc>.py`（complaint-service 位于 `tests/unit/`；唯一命名规避多服务 tests 包重名）
  - `deploy/prometheus/prometheus.yml`
  - `deploy/grafana/provisioning/datasources/prometheus.yml`
  - `deploy/grafana/provisioning/dashboards/provider.yml`
  - `deploy/grafana/provisioning/dashboards/ai-collection-overview.json`
- **修改文件**：
  - `common/llm/transport.py`（`post_chat_completions` 成功返回时把 usage 写入 ContextVar）
  - `common/llm/reliability.py`（新增 `_llm_last_usage` ContextVar 声明、`invoke_llm` 进入时 reset、`finally` 块中读取并清零（覆盖成功与失败/异常路径）、各状态埋点）
  - 8 个服务各自的 `app/main.py`（统一调 `mount_metrics`；mcp-gateway 迁到 common 帮助函数并废弃旧指标名）
  - `services/mcp-gateway/app/auth.py`（`/metrics` 从 `_protected_kind` 移除或短路放行）
  - `docker-compose.yml`（新增 prometheus、grafana 两服务）
  - `.env.example`（追加 `GRAFANA_ADMIN_PASSWORD=<change_me>`）
  - mcp-gateway 测试中引用 `mcp_gateway_requests_total` 的断言（最小更新为 `http_requests_total`）
  - `README.md` 8.2 表第 5、6 两行状态翻转
- **外部依赖**：新增 Docker 镜像 `prom/prometheus:v2.54.0`、`grafana/grafana:11.2.0`；**新增 Python 依赖 `prometheus-client`** 加入根 `requirements.in` 并重新生成 `requirements.lock`（当前根 lock 不含该包，仅 mcp-gateway 的 `requirements.runtime.txt` 单独声明；rag/qc/data-platform/call/case/review 无服务级 requirements 文件、全部只装根 lock，不加根 lock 会导致 7 个服务容器启动 ImportError——镜像漂移风险）；`structlog` 已在 common；新增 `.env` 字段 `GRAFANA_ADMIN_PASSWORD`。
- **运维方式变化**：本地启动命令由 `docker compose up -d` 变为 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`（如需抓全 8 服务）；Grafana 首次登录需用 `.env` 的 `GRAFANA_ADMIN_PASSWORD`。
- **不受影响**：所有业务逻辑；`complaint-service/app/llm/real_transport.py` 的限流/重试逻辑（P1#13 另开）；rag-service 的 `LLMFactory` 与 qc-service 的 `_as_transport` 内部实现；`docker-compose.complaint.yml` 双实例抓取（另开 change）；alertmanager / 日志采集 / tracing（列入 Non-Goals）；既有测试断言（除指标重命名最小更新外）。

## Non-Goals

- 不做告警：alertmanager、飞书/钉钉 webhook 均不做（README P1#10 单独批次）。
- 不做日志采集：filebeat / vector / ELK / Loki 不做（README P1#9）。
- 不修复 `complaint-service/app/llm/real_transport.py` 内嵌的自写限流/重试逻辑（README P1#13），另开 change；本 change 仅在 `post_chat_completions` 层埋 usage 点，不动 RealLLMTransport 的限流/重试代码路径。
- 不改造 rag-service 的 `LLMFactory` 与 qc-service 的 `_as_transport`——若它们底层不调 `post_chat_completions`，本期 `token_source=missing` 列入后续。
- 不做 `docker-compose.complaint.yml` 双实例（complaint-service-a/b）的 Prometheus 抓取——本期仅支持 `docker-compose.dev.yml` 单实例全栈，双实例抓取另开 change。
- 不做 OpenTelemetry / 分布式 tracing。
- 不做 Thanos / VictoriaMetrics 长期存储、远程写入。
- 不做业务指标（工单量、案件状态机、KB 文档数等）——本期只做"系统 RED + LLM 用量"两块。
- 不修改任何业务逻辑、不为指标重命名之外的断言修改既有测试。
- 不清理仓库存量 lint 债务（`make lint`：ruff 141 / bandit Low 8783 / mypy 15，均为本 change 之前既有、分布在其他模块）——本 change 触及的文件保证零新增（ruff 干净、mypy 无命中）；存量清理另开 change，不纳入本 change 范围。

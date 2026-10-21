## Purpose

平台级 HTTP 指标采集与 Prometheus/Grafana 接入能力：统一 8 个业务服务的 `/metrics` 暴露规范、纯 ASGI 中间件实现、跨 compose 的 Prometheus 抓取拓扑、内置 Grafana 服务红板 dashboard，为上生产提供最低限度的系统可观测性基线。

## ADDED Requirements

### Requirement: 统一 `/metrics` 端点暴露

所有 8 个业务服务（complaint-service、rag-service、qc-service、data-platform、mcp-gateway、call-service、case-service、review-service）SHALL 通过 `common/observability/metrics.py` 提供的 `mount_metrics(app, service_name)` 帮助函数暴露 `GET /metrics` 端点，返回 `prometheus_client.generate_latest()` 的原生 exposition 格式，Content-Type 为 `text/plain; version=1.0.0; charset=utf-8`（即 `prometheus_client.CONTENT_TYPE_LATEST` 常量值）。`/metrics` 响应 SHALL NOT 包装统一信封 `{"code","message","data","trace_id"}`。

#### Scenario: 业务服务暴露 `/metrics`

- **WHEN** 对 8 个服务任一宿主机映射端口发起 `GET /metrics`
- **THEN** 返回 HTTP 200，Content-Type 为 `text/plain; version=1.0.0; charset=utf-8`，body 包含 `http_requests_total` 指标，且 body 最外层不是 JSON 信封

#### Scenario: `/metrics` 不被鉴权拦截

- **WHEN** mcp-gateway 在 `ENABLE_AUTH=true` 模式下收到未带 Bearer 凭证的 `GET /metrics`
- **THEN** 返回 HTTP 200 而非 401（`/metrics` 从 `_protected_kind` 移除或短路放行）

### Requirement: 纯 ASGI MetricsMiddleware

`common/observability/metrics.py` SHALL 提供纯 ASGI 实现的 `MetricsMiddleware`（ SHALL NOT 使用 `starlette.middleware.base.BaseHTTPMiddleware`），以避免缓冲破坏 mcp-gateway `/mcp` 端点的 SSE 流。中间件 SHALL 覆盖所有 HTTP 请求，包括挂载的子应用（如 mcp-gateway 的 `app.mount("/", mcp.streamable_http_app())`）。

#### Scenario: SSE 流不被缓冲

- **WHEN** mcp-gateway 通过 `mount_metrics` 接入 common 中间件后，客户端经 `/mcp` 建立 streamable-http SSE 长连接
- **THEN** SSE 事件流即时推送，不出现缓冲导致的批量延迟到达

#### Scenario: 覆盖挂载子应用

- **WHEN** 对 mcp-gateway 的 `/mcp` 发起一次完整调用
- **THEN** `http_requests_total{svc="mcp-gateway",path="/mcp",...}` 计数 +1

### Requirement: HTTP 指标标签规范

`http_requests_total` SHALL 携带标签 `{svc, method, path, status}`；`http_request_duration_seconds` SHALL 携带标签 `{svc, method, path}`；`http_requests_in_flight` SHALL 携带标签 `{svc}`。`path` 标签 SHALL 走白名单模板（如 `/api/v1/complaints/{id}`），SHALL NOT 直接把原始请求路径写入标签，以避免高基数。

#### Scenario: path 高基数防护

- **WHEN** 连续请求 `/api/v1/complaints/111`、`/api/v1/complaints/222`、`/api/v1/complaints/333`
- **THEN** Prometheus 中只出现一个时间序列 `http_requests_total{svc="complaint-service",path="/api/v1/complaints/{id}",...}`，计数为 3，而非 3 个独立时间序列

#### Scenario: 未命中白名单的兜底

- **WHEN** 请求一个未在白名单中注册的路径
- **THEN** `path` 标签回退为字面值 `__unmatched__`（或服务自定义的安全兜底），不直接使用原始 path

### Requirement: mcp-gateway 指标名迁移

mcp-gateway SHALL 废弃旧指标 `mcp_gateway_requests_total{method,path,status}` 与 `mcp_gateway_active_connections`，迁移到统一指标 `http_requests_total{svc="mcp-gateway",method,path,status}` 与 `http_requests_in_flight{svc="mcp-gateway"}`。既有测试中引用旧指标名的断言 SHALL 做最小更新。

#### Scenario: 旧指标名消失

- **WHEN** mcp-gateway 启动后抓取 `/metrics`
- **THEN** body 中不存在 `mcp_gateway_requests_total` 与 `mcp_gateway_active_connections`，存在 `http_requests_total{svc="mcp-gateway",...}` 与 `http_requests_in_flight{svc="mcp-gateway"}`

### Requirement: Prometheus 抓取拓扑

主 `docker-compose.yml` SHALL 新增 `prometheus` 服务（镜像 `prom/prometheus:v2.54.0`，端口 `9090:9090`），挂载仓库内 `deploy/prometheus/prometheus.yml` 到 `/etc/prometheus/prometheus.yml`，并配置 `extra_hosts: host.docker.internal:host-gateway` 以兼容 Linux。抓取配置 SHALL 包含 9 个 job：1 个自抓 job（`job_name: prometheus`，目标 `localhost:9090`）+ 8 个业务服务 job，目标均为 `host.docker.internal:{宿主机端口}`。

#### Scenario: 9 个 target 全 UP

- **WHEN** 主 compose 与 `docker-compose.dev.yml` 单实例全栈均启动后，访问 `http://localhost:9090/targets`
- **THEN** 9 个 job（prometheus 自抓 + data-platform:8001 / rag-service:8002 / qc-service:8003 / call-service:8004 / case-service:8005 / complaint-service:8006 / review-service:8007 / mcp-gateway:9009）State 全部为 UP

### Requirement: Grafana 内置 Dashboard

主 `docker-compose.yml` SHALL 新增 `grafana` 服务（镜像 `grafana/grafana:11.2.0`，端口 `3000:3000`），通过 `./deploy/grafana/provisioning` 自动装配 datasource 与 dashboard。管理员密码 SHALL 从 `.env` 的 `GRAFANA_ADMIN_PASSWORD` 读取，`.env.example` SHALL 包含 `GRAFANA_ADMIN_PASSWORD=<change_me>` 占位。内置 dashboard SHALL 名为「AI-Collection Overview」，包含两个 section：服务 RED（Rate/Errors/Duration）与 LLM 用量（占位）。

#### Scenario: 首次登录即可看板

- **WHEN** 用户用 `.env` 中 `GRAFANA_ADMIN_PASSWORD` 登录 `http://localhost:3000`
- **THEN** 无需手工配置 datasource，直接打开「AI-Collection Overview」dashboard，两个 section 均正常渲染；mcp-gateway 与 complaint-service 面板有真实数据，其余服务允许 No data 但 panel 框架不报错

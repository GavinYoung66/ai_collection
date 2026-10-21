## MODIFIED Requirements

### Requirement: upstream 舱壁隔离与网关层错误统一信封

每个被代理的后端服务 MUST 使用独立的 upstream 与独立连接池；单个后端服务不可用（超时、连接拒绝）时，其故障 MUST NOT 影响其他路由的可用性。每个 upstream MUST 配置被动熔断参数 `max_fails=3 fail_timeout=10s`：连续 3 次失败后该 upstream 进入 10 秒熔断窗口，期间请求直接返回 502 而非逐次等待超时。非幂等方法（POST/PUT/DELETE/PATCH）MUST NOT 被自动重试到下一 upstream 节点（`proxy_next_upstream` 仅对幂等方法生效），避免重复写入。网关自身产生的 5xx（upstream 连接拒绝→502、upstream 读超时→504）MUST 返回统一 JSON 信封而非 Nginx 默认 HTML 错误页：502 映射 `code=B0008`（网关错误）、504 映射 `code=B0009`（服务暂不可用），并携带 `trace_id` 便于排查。

#### Scenario: 单服务故障不连累其他路由

- **WHEN** rag-service 停止且浏览器请求 `/api/v1/complaints` 路由
- **THEN** complaint-service 路由仍正常返回 200，仅 kb 相关路由返回网关层错误（504）

#### Scenario: 网关层 5xx 返回统一信封而非默认 HTML

- **WHEN** rag-service 停止（upstream 连接拒绝/读超时）时浏览器经网关请求 `/api/v1/kb/documents`
- **THEN** 网关返回 502 或 504，响应体为统一 JSON 信封（`code=B0008` 或 `B0009` + `trace_id`），MUST NOT 是 Nginx 默认 HTML 错误页

#### Scenario: 连续失败触发被动熔断快速失败

- **WHEN** 某 upstream 在 10 秒内连续 3 次连接失败/超时
- **THEN** 该 upstream 进入熔断窗口，期间对该 upstream 的请求直接快速返回 502（`code=B0008`），不再逐次等待至超时；窗口过期后自动恢复试探

#### Scenario: 非幂等请求不被自动重试

- **WHEN** 网关向某 upstream 发起 POST 请求时该 upstream 返回失败
- **THEN** 网关 MUST NOT 将该 POST 自动重试到下一节点（避免重复创建工单等副作用），直接返回失败信封；幂等 GET 在多节点 upstream 时可重试

### Requirement: CORS 与预检处理

网关 MUST 允许跨域访问并正确响应 `OPTIONS` 预检请求；`OPTIONS` 预检 MUST 由网关直接响应（不 `proxy_pass` 到后端），避免后端未实现 OPTIONS 而返回 405；允许的请求头 MUST 包含 `Authorization`、`Content-Type`、`X-Trace-Id`、`X-User-Id`。`Access-Control-Allow-Origin` MUST NOT 使用通配符 `*`，MUST 从 `.env` 的 `CORS_ALLOWED_ORIGINS`（逗号分隔白名单）中按请求 `Origin` 匹配后回显；未命中白名单的 Origin MUST NOT 返回 `Access-Control-Allow-Origin` 头。默认白名单须包含本地开发源（如 `http://localhost:8080`、`http://127.0.0.1:8080`）。

#### Scenario: 跨域预检通过

- **WHEN** 白名单内 Origin 的浏览器对网关发起 `OPTIONS` 预检
- **THEN** 网关返回允许的来源（回显该 Origin）、方法与头信息，预检通过后正式请求可正常转发

#### Scenario: OPTIONS 预检不透传到后端

- **WHEN** 浏览器对任意业务路由（如 `/api/v1/complaints`）发起 `OPTIONS` 预检
- **THEN** 网关直接返回预检响应，该请求 MUST NOT 被转发到对应后端服务（后端访问日志中无 OPTIONS 记录，后端不会因未实现 OPTIONS 而返回 405）

#### Scenario: 非白名单 Origin 被拒

- **WHEN** 请求 `Origin: https://evil.example.com`（不在 `CORS_ALLOWED_ORIGINS` 白名单内）发起跨域请求
- **THEN** 响应 MUST NOT 携带 `Access-Control-Allow-Origin` 头，浏览器因 CORS 策略拒绝读取响应

#### Scenario: 通配符不再使用

- **WHEN** 任意请求到达网关
- **THEN** 响应的 `Access-Control-Allow-Origin` MUST NOT 为字面量 `*`，MUST 为白名单中匹配到的具体 Origin 或不返回该头

### Requirement: 流式与长连接透传

对 Gradio 及流式接口，网关 MUST 支持 HTTP Upgrade/WebSocket 并关闭响应缓冲（proxy_buffering off），SSE 与 WebSocket 长连接 MUST NOT 被缓冲或被短请求超时提前切断。网关超时 MUST 按路由分级：健康检查（`/gateway/health`、`/health`）建连 3 秒、读 3 秒；普通读写接口（`/api/v1/complaints`、`/api/v1/qc`、`/api/v1/kb`、`/api/v1/sensitive-words`、`/api/v1/stats`、`/api/v1/mode`）建连 3 秒、读 15 秒；长任务/流式接口（`/rag/`、judge 类长轮询）建连 3 秒、读 120 秒。普通接口的 120 秒读超时 MUST NOT 应用到短请求路由导致资源占用。

#### Scenario: Gradio 长连接稳定

- **WHEN** 浏览器经 `:8080/rag/` 使用 Gradio 的流式输出或 WebSocket 能力
- **THEN** 连接保持、数据实时到达，不出现中断或被缓冲导致的延迟

#### Scenario: 普通接口超时分级

- **WHEN** 某普通读写接口（如 `/api/v1/qc/rules`）后端处理超过 15 秒
- **THEN** 网关在 15 秒后返回 504（`code=B0009`），而非等待 120 秒，避免短请求长时间占用连接

#### Scenario: 长任务接口不受短超时影响

- **WHEN** judge 类长任务接口处理时间在 15~120 秒之间
- **THEN** 网关不因 15 秒普通超时提前切断，允许其完成到 120 秒

## ADDED Requirements

### Requirement: 鉴权开关与身份透传

网关 MUST 支持可选的请求鉴权，由环境变量 `ENABLE_AUTH` 控制：`false`（默认）时网关不执行任何鉴权，所有请求直接转发，行为与无鉴权现状完全一致；`true` 时网关通过 Nginx `auth_request` 指令向内部鉴权端点（`/internal/authz`）发起子请求校验凭证，校验通过（204）才转发到后端并将身份头（`X-User-Id`、`X-User-Role`）透传给后端（透传 MUST 经 `auth_request_set` 捕获 sidecar 回传头到变量，再 `proxy_set_header` 下发——auth_request 子请求的响应头不会被自动转发），校验失败（401）直接返回统一信封 `code=B0002`（未认证）。`/gateway/health` 与 `/health` 健康检查端点、`OPTIONS` 预检请求、`/rag/`（Gradio 自有 `RAG_API_TOKEN` 鉴权）及其附属资源（`/manifest.json`）、纯静态前端壳（`/`、`/complaint/`、`/qc/`）MUST 永远豁免网关鉴权，不受 `ENABLE_AUTH` 影响。鉴权逻辑的验证契约由 `security/authn` 能力定义，网关仅负责调用与透传。

#### Scenario: 鉴权关闭时零回归

- **WHEN** `ENABLE_AUTH=false`（默认）时浏览器经网关请求任意业务路由
- **THEN** 请求直接转发到后端，无鉴权子请求，行为与无鉴权现状完全一致

#### Scenario: 鉴权开启时无凭证被拒

- **WHEN** `ENABLE_AUTH=true` 时请求未携带 `Authorization` 头
- **THEN** 网关返回 401 与统一信封（`code=B0002`），请求 MUST NOT 到达后端

#### Scenario: 鉴权开启时伪造凭证被拒

- **WHEN** `ENABLE_AUTH=true` 时请求携带伪造/过期的 JWT 或错误 API Key
- **THEN** 网关返回 401 与统一信封（`code=B0002`），请求 MUST NOT 到达后端

#### Scenario: 鉴权开启时合法凭证放行并透传身份

- **WHEN** `ENABLE_AUTH=true` 时请求携带合法 JWT/API Key
- **THEN** 网关转发请求到后端，且后端收到的请求头包含 `X-User-Id` 与 `X-User-Role`（由鉴权 sidecar 回传）

#### Scenario: 健康检查与预检永远豁免

- **WHEN** `ENABLE_AUTH=true` 时访问 `/gateway/health`、`/health` 或发起 `OPTIONS` 预检
- **THEN** 请求不经过鉴权子请求，直接返回健康/预检响应

### Requirement: TLS 与安全响应头

网关 MUST 对所有响应添加安全响应头：`X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`。当 `TLS_ENABLED=true` 时，网关 MUST 监听 8443 端口并挂载 TLS 证书（本地使用自签证书，生产替换正式证书），且将明文 8080 请求重定向到 8443 HTTPS；`TLS_ENABLED=false`（默认）时维持 8080 明文监听，不强制 HTTPS，保证本地开发体验。HTTPS 模式下 MUST 额外添加 `Strict-Transport-Security` 头（HSTS）。

#### Scenario: 安全响应头始终存在

- **WHEN** 任意请求到达网关并获得响应
- **THEN** 响应头包含 `X-Frame-Options: DENY`、`X-Content-Type-Options: nosniff`、`Referrer-Policy: strict-origin-when-cross-origin`

#### Scenario: TLS 关闭时维持明文

- **WHEN** `TLS_ENABLED=false`（默认）时浏览器访问 `http://<gateway>:8080`
- **THEN** 网关正常以明文 HTTP 服务，不强制跳转 HTTPS

#### Scenario: TLS 开启时强制 HTTPS

- **WHEN** `TLS_ENABLED=true` 时浏览器访问 `http://<gateway>:8080`
- **THEN** 网关返回重定向到 `https://<gateway>:8443`，且 HTTPS 响应携带 HSTS 头

### Requirement: 指标导出端点

网关 MUST 暴露 `/metrics` 端点供 Prometheus 抓取（实现拓扑：nginx 开 `stub_status`，新增独立 `nginx-prometheus-exporter` 容器抓取 stub_status 并转 Prometheus 格式，网关 `location /metrics` 反代到该 exporter，使 `:8080/metrics` 可用），内容至少包含 Nginx `stub_status` 提供的活跃连接数、总请求数、等待连接数等基础指标。`/metrics` 端点 MUST NOT 对外部裸奔：仅允许内网（如 docker network 内）访问，或在 `ENABLE_AUTH=true` 时要求鉴权。`/metrics` MUST NOT 被限流策略拦截。指标范围以 `stub_status` 原生能力为上限；各 upstream 维度的分状态码统计需 Lua/商业版模块，不在本 Requirement 承诺范围内。

#### Scenario: 指标端点可被抓取

- **WHEN** Prometheus 从内网访问 `http://<gateway>:8080/metrics`
- **THEN** 返回 200 与 Prometheus 文本格式指标，含活跃连接数等基础指标

#### Scenario: 指标端点不对外裸奔

- **WHEN** 外部（非内网/未鉴权）请求访问 `/metrics`
- **THEN** 返回 401 或 403，MUST NOT 暴露指标内容

#### Scenario: 指标端点不受限流影响

- **WHEN** 高频抓取 `/metrics`
- **THEN** 不触发 429 限流，保证监控数据连续性

### Requirement: 灰度路由开关

网关 MUST 支持基于权重的灰度路由能力，由 `CANARY_WEIGHT` 环境变量（0~100）控制：`0`（默认）时所有流量走 v1 upstream；`>0` 时按权重比例将流量分配到 v1 与 v2 两个 upstream。当前仅部署 v1，v2 upstream 指向与 v1 相同的后端（占位），`CANARY_WEIGHT` 默认为 0，灰度能力处于"开关备好、未启用"状态。灰度分流 MUST 基于 `split_clients` 按稳定会话标识（如 `$cookie_canary` 或客户端持续携带的 `X-Trace-Id`）分配，同一会话的请求始终路由到同一版本；MUST NOT 以 nginx 自动注入的 `$request_id`/`$trace_id` 作为分流键——这些值每请求唯一，会导致同一会话散到不同版本。

#### Scenario: 灰度关闭时全走 v1

- **WHEN** `CANARY_WEIGHT=0`（默认）时请求到达网关
- **THEN** 100% 流量路由到 v1 upstream，v2 不接收任何请求

#### Scenario: 灰度开启时按权重分流

- **WHEN** `CANARY_WEIGHT=5` 时持续请求某路由
- **THEN** 约 5% 请求路由到 v2，约 95% 路由到 v1，且同一会话的请求稳定路由到同一版本

### Requirement: 共享网络与配置模板化部署

网关与 complaint-service、qc-service、rag-service MUST 接入同一个共享 docker network（external network），使网关 upstream 可直接以「compose 服务名:容器内端口 8000」寻址，MUST NOT 依赖 `host.docker.internal` 或宿主机端口映射。共享网络 MUST 由 compose 声明接入，使各服务以 compose 服务名作为网络别名注册到该网络；MUST NOT 仅以手工 `docker network connect` 方式接入——手工接入不注册服务名别名，会出现「网络存在、容器在列，但服务名无法解析」的空转状态，网关连通性实际上仍由「恰好同属一个 compose project 的默认网络」提供，跨环境即失效。`nginx.conf` MUST 改为 `nginx.conf.template` 模板，容器启动时通过 `envsubst` 从 `.env` 渲染为最终配置；渲染 MUST 使用显式变量白名单（`envsubst '${VAR1} ${VAR2} …'`），MUST NOT 让 envsubst 替换 nginx 内置变量（`$host`/`$remote_addr`/`$status`/`$request_id`/`$http_*` 等）：这些内置变量非容器环境变量，被 envsubst 替换为空串将导致 trace 透传、限流键、日志格式失效；上游地址、CORS 白名单、`ENABLE_AUTH`、`TLS_ENABLED`、`CANARY_WEIGHT`、`LOG_JSON` 等所有可变值 MUST 来自 `.env`，`.env` 为唯一事实源，全仓 MUST NOT 存在第二份硬编码 upstream 地址。

#### Scenario: 网关经共享网络直连容器

- **WHEN** 停止宿主机业务端口映射（如不再 publish 8006:8000），仅保留共享 docker network
- **THEN** 经网关 8080 仍能调通 complaint-service/qc-service/rag-service（upstream 以容器服务名寻址）

#### Scenario: 共享网络可解析服务名

- **WHEN** 一个仅接入共享网络的隔离容器尝试解析 `complaint-service` / `qc-service` / `rag-service`
- **THEN** 解析成功并返回容器 IP，MUST NOT 出现 `Name or service not known`；网关的 upstream 连通性 MUST 由该共享网络提供，MUST NOT 仅因网关与后端恰好同属一个 compose project 的默认网络而可用

#### Scenario: 配置由 .env 渲染

- **WHEN** 修改 `.env` 中某 upstream 地址或开关后重启网关容器
- **THEN** 新配置生效，nginx.conf 反映新值，MUST NOT 需要手动编辑 nginx.conf

#### Scenario: 全仓无第二份硬编码 upstream 地址

- **WHEN** 在 deploy/gateway 与各服务 config 中搜索 upstream 地址
- **THEN** 地址仅出现在 `.env`（唯一事实源）与渲染后的 nginx.conf（由模板生成），不存在手写的第二份硬编码

#### Scenario: 渲染保留 nginx 内置变量

- **WHEN** 容器启动时用 envsubst 渲染 nginx.conf.template
- **THEN** 渲染后 nginx.conf 中 nginx 内置变量（`$host`、`$remote_addr`、`$status`、`$request_id`、`$http_x_trace_id`）保持原样，仅白名单内的 `${VAR}`（如 `${UPSTREAM_COMPLAINT}`、`${ENABLE_AUTH}`）被替换为 .env 值

### Requirement: JSON 访问日志

网关 MUST 保留结构化访问日志并支持 JSON 输出，由 `LOG_JSON` 环境变量控制：`false`（默认）时使用现有 `gateway` 文本日志格式，`true` 时输出单行 JSON。访问日志 MUST 包含 `trace_id`、状态码、`request_time`、`upstream_response_time`、`body_bytes_sent` 等字段，使下游日志采集可通过 `trace_id` 与各服务日志关联。

#### Scenario: 默认文本格式

- **WHEN** `LOG_JSON=false`（默认）时请求到达网关
- **THEN** 访问日志以现有 `gateway` 文本格式输出，含 `trace_id` 与 `request_time`

#### Scenario: 开启 JSON 格式

- **WHEN** `LOG_JSON=true` 时请求到达网关
- **THEN** 访问日志为单行 JSON，含 `trace_id`、`status`、`request_time`、`upstream_response_time`、`body_bytes_sent` 字段，可被日志采集系统结构化解析

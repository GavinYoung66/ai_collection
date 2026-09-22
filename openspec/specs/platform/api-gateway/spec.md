# platform/api-gateway

## Purpose

为浏览器与演示侧提供单一北向 HTTP 入口：按 URL 路径反向代理到各后端服务，统一限流、CORS、trace 透传并做 upstream 舱壁隔离。网关只做转发与横切治理，不解析业务 payload，不承载任何业务逻辑。

## Requirements

### Requirement: 单入口路径路由

网关 SHALL 在统一端口（8080）提供唯一北向入口，并按 URL 前缀（前缀匹配，不带尾斜杠，须同时覆盖无尾斜杠的资源根路径与其下子路径）将请求反代到对应后端服务：`/api/v1/complaints`（含创建工单的 `POST /api/v1/complaints` 根路径与子资源）、`/api/v1/stats`、`/api/v1/mode` 路由到 complaint-service；`/api/v1/qc` 路由到 qc-service；`/api/v1/kb` 与 `/api/v1/sensitive-words` 路由到 rag-service；`/rag/` 路由到 Gradio 前端。Nginx location 匹配须保证长前缀（`/api/v1/...`、`/rag/`）优先于 `/` 兜底（导航页），避免因 location 顺序错误导致业务路由被 `/` 截获。经网关转发后的响应（状态码、统一响应信封、响应体）MUST 与直连对应后端端口完全一致。网关 MUST 将请求体上限（`client_max_body_size`）设为不小于 100MB 以放行多模态文件上传（PPTX/图片/音频），MUST NOT 使用 Nginx 默认 1MB 上限在后端校验之前拦截请求；文件大小的业务合法性仍由后端按自身上限（rag-service `KB_MAX_FILE_SIZE` 默认 20MB）判定。

#### Scenario: 前端只知一个 origin 访问质检接口

- **WHEN** 浏览器访问 `http://<gateway>:8080/api/v1/qc/rules/versions`
- **THEN** 请求被转发到 qc-service，返回结果与直连 qc-service 端口完全一致

#### Scenario: 同一入口访问三类后端服务

- **WHEN** 浏览器经 8080 分别请求 complaints、qc、kb 三类路由
- **THEN** 三类请求分别到达 complaint-service、qc-service、rag-service，且均返回正确业务响应

#### Scenario: 无尾斜杠的工单创建根路径可命中

- **WHEN** 数据中台/浏览器对 `http://<gateway>:8080/api/v1/complaints`（无尾斜杠）发起 POST 创建工单
- **THEN** 请求被正确路由到 complaint-service 并返回受理响应，不因缺少尾斜杠而 404

#### Scenario: Gradio 经子路径访问

- **WHEN** 浏览器访问 `http://<gateway>:8080/rag/`
- **THEN** Gradio 页面及其静态资源、接口在该子路径下正常加载，无资源路径 404

#### Scenario: 多模态文件经网关上传不被 413 拦截

- **WHEN** 浏览器经 8080 向 `/api/v1/kb/documents` 上传一个数 MB（≤ 后端 20MB 上限）的 PPTX/图片/音频文件
- **THEN** 请求体不被网关默认 1MB 上限拦截（无 413），到达 rag-service 并获得与直连 8002 一致的受理响应

#### Scenario: 超大文件由后端而非网关裁决

- **WHEN** 上传文件大于后端业务上限（20MB）但小于网关放行上限（100MB）
- **THEN** 网关正常透传，由 rag-service 返回业务超限错误（B0001 信封），而非网关层 413 HTML

#### Scenario: 原始 URI 不被网关改写

- **WHEN** 浏览器请求 `http://<gateway>:8080/api/v1/qc/rules/versions`
- **THEN** 后端收到的请求路径仍为 `/api/v1/qc/rules/versions`（网关 MUST NOT 改写或剥离 URI 前缀），与直连 8003 的路径一致；实现上各 location 须用 `proxy_pass http://<upstream>;`（不带尾路径），MUST NOT 用带尾斜杠的 `proxy_pass http://<upstream>/;`

### Requirement: upstream 舱壁隔离与网关层错误统一信封

每个被代理的后端服务 MUST 使用独立的 upstream 与独立连接池；单个后端服务不可用（超时、连接拒绝）时，其故障 MUST NOT 影响其他路由的可用性。网关自身产生的 5xx（upstream 连接拒绝→502、upstream 读超时→504）MUST 返回统一 JSON 信封而非 Nginx 默认 HTML 错误页：502 映射 `code=B0008`（网关错误）、504 映射 `code=B0009`（服务暂不可用），并携带 `trace_id` 便于排查。

#### Scenario: 单服务故障不连累其他路由

- **WHEN** rag-service 停止且浏览器请求 `/api/v1/complaints` 路由
- **THEN** complaint-service 路由仍正常返回 200，仅 kb 相关路由返回网关层错误（504）

#### Scenario: 网关层 5xx 返回统一信封而非默认 HTML

- **WHEN** rag-service 停止（upstream 连接拒绝/读超时）时浏览器经网关请求 `/api/v1/kb/documents`
- **THEN** 网关返回 502 或 504，响应体为统一 JSON 信封（`code=B0008` 或 `B0009` + `trace_id`），MUST NOT 是 Nginx 默认 HTML 错误页

### Requirement: 全局限流与统一错误信封

网关 MUST 对北向请求按来源 IP 做全局限流，默认阈值 20 请求/秒、突发容量 burst 40。超出阈值的请求 MUST 返回 HTTP 429（与项目 `B0006` 限流语义一致；实现须显式 `limit_req_status 429` + `error_page 429` 返回 JSON，覆盖 Nginx `limit_req` 默认的 503）与统一 JSON 信封（`code` 为 `B0006`，并带 `Retry-After` 提示），MUST NOT 返回 Nginx 默认 503 HTML 错误页。

#### Scenario: 请求频率超限

- **WHEN** 同一来源在 1 秒内对同一路由发出超过阈值的大量请求
- **THEN** 超出部分返回 HTTP 429 与统一信封 JSON（`code=B0006`），响应体不是 Nginx 默认 503 HTML，且包含可重试提示

#### Scenario: 正常频率不受影响

- **WHEN** 来源 IP 以低于阈值的正常频率请求
- **THEN** 请求全部正常转发，不被限流拦截

### Requirement: CORS 与预检处理

网关 MUST 允许同源与跨域演示访问，并正确响应 `OPTIONS` 预检请求；`OPTIONS` 预检 MUST 由网关直接响应（不 `proxy_pass` 到后端），避免后端未实现 OPTIONS 而返回 405；允许的请求头 MUST 包含 `Authorization`、`Content-Type`、`X-Trace-Id`、`X-User-Id`。

#### Scenario: 跨域预检通过

- **WHEN** 浏览器对网关发起 `OPTIONS` 预检
- **THEN** 网关返回允许的来源、方法与头信息，预检通过后正式请求可正常转发

#### Scenario: OPTIONS 预检不透传到后端

- **WHEN** 浏览器对任意业务路由（如 `/api/v1/complaints`）发起 `OPTIONS` 预检
- **THEN** 网关直接返回预检响应，该请求 MUST NOT 被转发到对应后端服务（后端访问日志中无 OPTIONS 记录，后端不会因未实现 OPTIONS 而返回 405）

### Requirement: Trace 标识透传与注入

请求携带 `X-Trace-Id` 时网关 MUST 原样透传给下游；请求未携带时网关 MUST 生成一个 trace 标识并注入到转发请求，使下游日志与网关访问日志可通过该标识关联。

#### Scenario: 透传已有 trace

- **WHEN** 请求头已带 `X-Trace-Id: abc123`
- **THEN** 下游服务收到的请求头中 `X-Trace-Id` 仍为 `abc123`

#### Scenario: 缺失时注入 trace

- **WHEN** 请求未携带 `X-Trace-Id`
- **THEN** 网关注入生成的 trace 标识，下游日志中可看到该标识

### Requirement: 流式与长连接透传

对 Gradio 及流式接口，网关 MUST 支持 HTTP Upgrade/WebSocket 并关闭响应缓冲（proxy_buffering off），SSE 与 WebSocket 长连接 MUST NOT 被缓冲或被短请求超时提前切断；网关超时 MUST 分级（建连 3 秒、读 120 秒）。

#### Scenario: Gradio 长连接稳定

- **WHEN** 浏览器经 `:8080/rag/` 使用 Gradio 的流式输出或 WebSocket 能力
- **THEN** 连接保持、数据实时到达，不出现中断或被缓冲导致的延迟

### Requirement: 只做转发不做业务

网关 MUST NOT 解析或改写业务 payload，MUST NOT 解析 MCP tool 调用，MUST NOT 实现判责、质检规则或检索逻辑；服务间容器网络内部调用（如 Gradio→rag-service、qc→rag）MUST 保持原有直连，不被强制绕行网关。

#### Scenario: 请求体不被业务化处理

- **WHEN** 任意业务请求（含投诉判责、质检、检索）经过网关
- **THEN** 网关仅依据路径与头部转发，不解析请求体语义，不产生业务判定

### Requirement: 静态演示页同源托管

网关 SHOULD 同源托管投诉与质检的演示静态页面及一个导航入口页；演示页默认的后端地址 MUST 为同源相对路径 `/api/v1`，不再硬编码具体后端端口，使用户只通过网关 origin 即可完成完整演示。

#### Scenario: 一个地址完成端到端演示

- **WHEN** 用户访问网关下的投诉页与质检页并操作
- **THEN** 页面发出的 API 请求经同一 origin（网关）完成，页面默认配置与运行时请求中不存在 8006/8003 等硬编码端口

#### Scenario: 网关自身存活检查

- **WHEN** 访问 `/gateway/health`
- **THEN** 返回 200 与存活标识，用于容器健康检查与穿透工具探活

# Spec Delta

## MODIFIED Requirements

### Requirement: 单入口路径路由

网关 SHALL 在统一端口（8080）提供唯一北向入口，并按 URL 前缀（前缀匹配，不带尾斜杠，须同时覆盖无尾斜杠的资源根路径与其下子路径）将请求反代到对应后端服务：`/api/v1/complaints`（含创建工单的 `POST /api/v1/complaints` 根路径与子资源）、`/api/v1/stats`、`/api/v1/mode` 路由到 complaint-service；`/api/v1/qc` 路由到 qc-service；`/api/v1/kb` 与 `/api/v1/sensitive-words` 路由到 rag-service；`/rag/` 路由到 Gradio 前端；**`/api/v1/auth`（含 `/login` 与 `/me`，单 location 前缀匹配、不挂 `auth_request`）路由到 auth-sidecar**（新增）。Nginx location 匹配须保证长前缀（`/api/v1/...`、`/rag/`）优先于 `/` 兜底（导航页），避免因 location 顺序错误导致业务路由被 `/` 截获。经网关转发后的响应（状态码、统一响应信封、响应体）MUST 与直连对应后端端口完全一致。网关 MUST 将请求体上限（`client_max_body_size`）设为不小于 100MB 以放行多模态文件上传（PPTX/图片/音频），MUST NOT 使用 Nginx 默认 1MB 上限在后端校验之前拦截请求；文件大小的业务合法性仍由后端按自身上限（rag-service `KB_MAX_FILE_SIZE` 默认 20MB）判定。

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

#### Scenario: auth/login 反代 auth-sidecar 且豁免鉴权

- **WHEN** ENABLE_AUTH=true，请求 `POST /api/v1/auth/login`，正确凭证
- **THEN** 请求到达 auth-sidecar（upstream `auth`），返回 HTTP 200 + 统一信封含 JWT；auth-sidecar 需具备 DB 连接能力以查 users 表

#### Scenario: auth/me 反代 auth-sidecar 且豁免鉴权

- **WHEN** ENABLE_AUTH=true，请求 `GET /api/v1/auth/me` 携带有效 Bearer token
- **THEN** 请求到达 auth-sidecar，返回 HTTP 200 + 当前用户身份（auth-sidecar 按 JWT `sub` 查库复核 `status` 与 `role`，`status != active` 返回 403 `B0003`）

#### Scenario: ENABLE_AUTH=false 时 auth 端点仍正常

- **WHEN** ENABLE_AUTH=false（默认），请求 `POST /api/v1/auth/login`
- **THEN** 与 ENABLE_AUTH=true 时行为完全一致——auth 端点永远可用，不受鉴权开关影响

#### Scenario: auth-sidecar 不可用时 auth 端点返回 502

- **WHEN** auth-sidecar 容器挂了，请求 `POST /api/v1/auth/login`
- **THEN** 返回 HTTP 502（upstream `auth` 被动熔断），网关错误信封（`code=B0008`）

### Requirement: 鉴权开关与身份透传

网关 MUST 支持可选的请求鉴权，由环境变量 `ENABLE_AUTH` 控制：`false`（默认）时网关不执行任何鉴权，所有请求直接转发，行为与无鉴权现状完全一致；`true` 时网关通过 Nginx `auth_request` 指令向内部鉴权端点（`/internal/authz`）发起子请求校验凭证，校验通过（204）才转发到后端并将身份头（`X-User-Id`、`X-User-Role`）透传给后端（透传 MUST 经 `auth_request_set` 捕获 sidecar 回传头到变量，再 `proxy_set_header` 下发——auth_request 子请求的响应头不会被自动转发），校验失败（401）直接返回统一信封 `code=B0002`（未认证）。auth 豁免清单扩展为：`/gateway/health` 与 `/health` 健康检查端点、`OPTIONS` 预检请求、`/rag/`（Gradio 自有 `RAG_API_TOKEN` 鉴权）及其附属资源（`/manifest.json`）、纯静态前端壳（`/`、`/complaint/`、`/qc/`）、**`/api/v1/auth/login` 与 `/api/v1/auth/me`**（新增——登录本身就是获取 token 的入口，me 端点仅需 auth-sidecar 自身验证 JWT 有效即可，不经网关 auth_request 子请求）——以上所有端点 MUST 永远豁免网关鉴权，不受 `ENABLE_AUTH` 影响。鉴权逻辑的验证契约由 `security/authn` 能力定义，网关仅负责调用与透传。

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

#### Scenario: ENABLE_AUTH=true 时业务路由被拦截但 auth 路由豁免

- **WHEN** ENABLE_AUTH=true，无 Bearer token，请求 `GET /api/v1/complaints`
- **THEN** 返回 HTTP 401（网关 auth_request 拒绝）
- **WHEN** 同条件下请求 `POST /api/v1/auth/login`（正确凭证）
- **THEN** 请求正常转发到 auth-sidecar，返回 HTTP 200 + JWT（登录成功）或 auth-sidecar 自身的 401（密码错误），而非网关层的 401
- **WHEN** ENABLE_AUTH=true，无 Bearer token，请求 `GET /api/v1/auth/me`
- **THEN** 请求转发到 auth-sidecar，返回 auth-sidecar 自身的 401（JWT 验证失败），而非网关层的 401

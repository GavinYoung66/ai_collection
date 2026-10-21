## Why

当前 Nginx 网关（deploy/gateway/nginx.conf + docker-compose.gateway.yml，监听 8080）作为唯一北向入口已具备路由转发、限流、CORS、trace 透传、统一错误信封、静态托管等能力，但存在生产化硬伤：①upstream 用 `host.docker.internal:800x` 绕宿主机端口映射，跨环境（纯容器/k8s）不可用，根因是网关 compose 未接入业务 docker network；②无认证鉴权、CORS 为 `*`；③上游地址在 nginx.conf 与各服务 config.py 双份硬编码易漂移；④无 TLS/安全响应头；⑤upstream 无被动熔断、超时统一 120s 未按路由分级；⑥无指标端点；⑦无灰度开关。本 change 对网关做一次性加固，使其达到可生产部署门槛，同时保持本地开发体验零回归。

## What Changes

- **共享网络收口**：定义统一 external docker network，网关与 complaint-service/qc-service/rag-service 接入；upstream 改为「compose 服务名:容器内端口 8000」，删除 `host.docker.internal` 依赖。网关路由不再依赖宿主机端口映射，二者解耦。
- **nginx 模板化**：`nginx.conf` 改为 `nginx.conf.template`，启动时 envsubst 从 `.env` 渲染；上游地址、CORS 白名单、各开关均来自 `.env`，使其成为唯一事实源，消除双份硬编码。渲染用显式变量白名单，避免 envsubst 误替换 nginx 内置变量（`$host`/`$status` 等）；条件块（TLS/auth_request）由入口脚本按开关拼接，不经 envsubst。
- **鉴权（完整实现 + 单测，默认关闭）**：验证逻辑下沉 `common/security/authn.py`（JWT 验签/过期/issuer/audience + API Key 恒定时间比对），其中 JWT 复用并扩展 `rbac.py` 现有验签能力（补 iss/aud 与拒绝原因，不另起一套 JWT 解码）；极薄 sidecar（`deploy/auth`，FastAPI <150 行）暴露 `/internal/authz`；nginx 用 `auth_request` 调用，由 `ENABLE_AUTH` 控制（false 默认，本地零回归；true 启用）；`/gateway/health`、`/health` 与 OPTIONS 预检永远豁免。
- **CORS 收紧**：`*` 改为 `.env` 白名单 `CORS_ALLOWED_ORIGINS`（默认含本地开发源）。
- **TLS/安全头**：加 `X-Frame-Options`、`X-Content-Type-Options`、`Referrer-Policy`（HTTPS 下加 HSTS）；`TLS_ENABLED=true` 时监听 8443 + 证书挂载（本地自签/生产换正式证书）并由明文跳转，false 时维持 8080。
- **被动熔断**：upstream 加 `max_fails=3 fail_timeout=10s`；显式声明非幂等 POST 不转发下一节点。
- **超时分级**：health 3s / 普通读写 15s / judge 等长任务 120s，按 location 区分。
- **指标导出**：开 `stub_status` + nginx prometheus exporter，`/metrics` 仅内网/鉴权可达；指标范围据实限定为活跃连接数、总请求数等 stub_status 可提供项（各 upstream 分状态码需 Lua/商业版，列为后续）。
- **JSON 日志**：保留 trace_id 结构化格式，`LOG_JSON` 开关切换 JSON 输出。
- **灰度开关（只留开关，不启用）**：`split_clients` + `CANARY_WEIGHT` + v1/v2 双 upstream，weight=0 全走 v1；当前不部署 v2。

非目标：不改 7 服务业务编排与业务代码（common/security 新增 authn、sidecar 除外）；不变更东西向调用方式（MCP/httpx/EventBus 原样），内部调用韧性仍由各调用方负责；不做配置中心（Apollo/Nacos 属独立 change）；不做服务发现（docker DNS 已够）；data-platform 默认不对外。

## Capabilities

### New Capabilities
- `security/authn`: 网关鉴权的公共验证逻辑契约——`common/security/authn.py` 的 JWT/API Key 验证、恒定时间比对、身份头（X-User-Id/X-User-Role）下发；sidecar `/internal/authz` 契约与豁免清单。该能力可被网关与任意 Python 服务复用，保证全平台单一实现。

### Modified Capabilities
- `platform/api-gateway`: 在现有路由/限流/CORS/trace/错误信封基础上，新增 auth_request 鉴权开关、CORS 白名单收紧、TLS/安全头、被动熔断、超时分级、metrics 端点、灰度开关、共享网络与模板化部署等 Requirement；既有 Requirement（单入口路由、舱壁隔离、全局限流、CORS 预检、trace 透传、流式透传、只做转发、静态托管）的语义保持不变，仅在实现层面由模板化与共享网络承载。

## Impact

- **代码**：`deploy/gateway/`（nginx.conf.template + envsubst 启动脚本 + www 不变）、`deploy/auth/`（新增 sidecar + Dockerfile）、`common/security/authn.py`（新增）+ `common/security/tests/`（新增单测）、`common/security/rbac.py`（被复用，不改）、`docker-compose.gateway.yml`（接入共享网络 + sidecar 服务）、`docker-compose.dev.yml`/`docker-compose.complaint.yml`（业务服务接入共享网络）、`.env`/`.env.example`（新增 ENABLE_AUTH/TLS_ENABLED/CORS_ALLOWED_ORIGINS/CANARY_WEIGHT/JWT_*/LOG_JSON 等）。
- **API**：外部行为在 `ENABLE_AUTH=false`（默认）时零回归；`ENABLE_AUTH=true` 时未带/伪造凭证返 401，合法凭证透传身份头。新增 `/metrics`（仅内网/鉴权）。
- **依赖**：新增 nginx prometheus exporter（或 sidecar 暴露）；sidecar 用 python:3.x-slim 镜像 + 挂载 common 代码，不引入业务服务新依赖。
- **不碰**：7 个服务业务代码、eventbus、openspec/specs 主 spec（delta 为唯一事实源）、既有单测断言。

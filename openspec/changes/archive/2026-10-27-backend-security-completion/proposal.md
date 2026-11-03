## Why

前序 change `frontend-security-hardening`（2026-10-27 归档）在前端侧完成了五项安全加固（网关 CSP、sessionStorage 迁移、协议白名单、npm audit 门禁、后端预留钩子），其中 REQ-5a/5b/5c 在前端仅做了空实现预留（`registerErrorReporter` 导出、B0005 错误分支占位、`refresh_token` 空字段 + 401 刷新预留注释），后端未落地对应接口。此外，架构文档 §5.0 F 表明确指出 complaint-service 的 `PUT /review` / `PUT /close` 端点无角色保护（仅验 token），P1 系统管理的 admin 用户 CRUD 尚无后端接口，网关残留 `/api/v1/stats` 与 `/api/v1/mode` 两条孤儿路由。本 change 补齐上述五项后端缺口，使前端预留钩子从死代码变为实际生效逻辑。

## What Changes

**统一原则**：所有后端改动均在既有服务内扩展，不新建独立服务目录。auth-sidecar 作为本 change 的主要承载者。

- **REQ-5a 前端错误审计接口**（扩展 auth-sidecar）：新增 `POST /api/v1/audit/frontend-error` 端点，接收前端 JS 运行时错误（error_type、message、stack_trace、url、line、col、user_agent、trace_id），写入既有 `audit_logs` 表（`action="frontend_error"`，`detail` JSONB 承载 payload，`ip_address` 由网关透传或 auth-sidecar 自取）。需 JWT 鉴权（任意已认证用户），响应固定 `{code:0, data:{logged:true}}`。复用 `common/db/models/audit.py` 的 `AuditLog` ORM，不新建表。
- **REQ-5b 登录失败计数与账号锁定**（扩展 auth-sidecar）：在 login 流程前加 Redis 失败计数器（key `auth:login_fail:{username}`，TTL 15min），密码校验失败时 INCR。计数 ≥ 5 时：① 将 `users.status` 置为 `"locked"`；② 返回 **HTTP 423 + `B0011` + "账号已锁定，请15分钟后重试"**（注意：B0005 已被 complaint-service 用于"资源冲突/同工单重复提交"，不可复用；本 change 新增 ErrorCode.B0011 并补 ERROR_MESSAGES）。15 分钟后 Redis key 过期自动解锁，或由管理员手动改 status 恢复。
- **REQ-5c Refresh Token 机制**（扩展 auth-sidecar）：login 成功时新增签发 `refresh_token`（载荷仅含 `sub`，无 role，有效期 7 天；存储于 login 响应，服务端不持久化）；新增 `POST /api/v1/auth/refresh` 端点（Bearer 方式携带 refresh_token，验证 sub 存在且用户 status=active，签发新 access_token）；JWT 载荷 `sub`/`role`/`exp` 不变，refresh_token 新增 `type:"refresh"` claim 便于区分；前端 security-hardening 预留的 `auth.ts.refresh_token` 字段与 request.ts 401 刷新注释至此可移除预留改为生效实现。
- **P1 Admin 用户 CRUD**（扩展 auth-sidecar）：新增 `GET/POST/PUT/DELETE /api/v1/admin/users` 全接口，role allowlist `{"admin"}`（`require_roles` 复用 qc-service 的实现模式或抽 common 能力）。全部复用 `common/db/models/user.py` 的 User ORM。字段：username(unique)、password_hash(bcrypt cost=12)、real_name、role(5 种规范值之一)、status(active/disabled/locked)。密码明文入参，auth-sidecar 内部 bcrypt 哈希后存储，响应不返回 password_hash。
- **网关孤儿路由清理**（修改 deploy/gateway/server_common.conf.template）：删除 `location /api/v1/stats` 与 `location /api/v1/mode` 两条孤儿路由（后端真实路径为 `/api/v1/complaints/stats` 与 `PUT /api/v1/complaints/mode`，孤儿路由不落在 `/api/v1/complaints` 前缀内，请求落 `/` → HTML 404 非信封）。不影响任何现存正确路径。

**B0011 新增说明**：`common/errors/codes.py` 当前 B 段 B0001~B0010 已占，新增 `B0011 = "B0011"` comment "账号已锁定"，ERROR_MESSAGES 补 `"B0011": "账号已锁定，请15分钟后重试"`。前端 Login.vue 原预留 B0005 分支改为 B0011，error-map.ts 同步补 `"B0011": "账号已锁定，请15分钟后重试"`。complaint-service 的 B0005（同工单重复提交）保持不变。

## Capabilities

### New Capabilities
- `auth-sidecar/frontend-error-audit`: 前端运行时错误审计上报契约——auth-sidecar 的 `POST /api/v1/audit/frontend-error` 端点，写入 `audit_logs` 表（action="frontend_error"），前端通过已预留的 `registerErrorReporter` 钩子调用。
- `auth-sidecar/login-lockout`: 登录失败 Redis 计数器与账号锁定机制——auth-sidecar 的 login 流程前置 Redis 计数（key `auth:login_fail:{username}`，TTL 15min，阈值 5），达到阈值置 User.status="locked" 返回 B0011。
- `auth-sidecar/refresh-token`: Refresh Token 签发/校验/自动续期——auth-sidecar login 响应新增 refresh_token（type="refresh" claim，7 天有效期），新增 `/api/v1/auth/refresh` 端点。
- `auth-sidecar/admin-user-crud`: admin 专属用户 CRUD——auth-sidecar 的 `GET/POST/PUT/DELETE /api/v1/admin/users`，role allowlist {"admin"}。
- `common/errors/B0011`: 新增系统级错误码 B0011（账号已锁定），与 B0005（资源冲突）语义彻底分离。

### Modified Capabilities
- `deploy/gateway/server_common`: 删除 `/api/v1/stats` 与 `/api/v1/mode` 两条孤儿 location；其余路由与安全头配置不变。
- `common/errors/codes`: B 段新增 B0011；ERROR_MESSAGES 补 B0011 消息。
- `frontend-security-hardening REQ-5b`: 原预留的 Login.vue B0005 分支改为 B0011（与后端实际返回码对齐）。
- `frontend-security-hardening REQ-5c`: 原 refresh_token 预留字段与 401 刷新注释改为生效实现（request.ts 在 401 分支前插入 refresh 逻辑）。

## Impact

- **代码（auth-sidecar 扩展）**：`deploy/auth/main.py` 新增 4 端点（audit/frontend-error、auth/refresh、admin/users GET/POST/PUT/DELETE）；login 流程前置 Redis 计数器与 B0011 分支；新增 admin role guard。
- **代码（common 层）**：`common/errors/codes.py` 新增 B0011 enum + ERROR_MESSAGES 条目；无其他 common 模块改动。
- **代码（网关）**：`deploy/gateway/server_common.conf.template` 删除 2 条 orphan location。
- **代码（前端对齐）**：`frontend/src/app/pages/Login.vue` B0005→B0011；`frontend/src/shared/utils/error-map.ts` 补 B0011 映射；`frontend/src/shared/request.ts` 401 分支前插入 refresh_token 刷新逻辑；`frontend/src/app/stores/auth.ts` refresh_token 字段从空 null 改为 login 响应真实值存储。
- **API 新增**：`POST /api/v1/audit/frontend-error`、`POST /api/v1/auth/refresh`、`GET/POST/PUT/DELETE /api/v1/admin/users`；login 响应新增 `refresh_token` + `token_type:"refresh"`。
- **无破坏性变更**：complaint-service B0005 不受影响；既有 auth endpoints 路径/信封/成功码 int 0 全部保持不变；新增 error code B0011 仅新增不修改既有。
- **依赖**：复用已有的 `common/resilience/_redis.py`（get_redis）、`common/db/models/audit.py`（AuditLog）、`common/db/models/user.py`（User）、`common/security/rbac.py`（create_token/verify_token_with_reason）；auth-sidecar Dockerfile 需确认 python-redis 依赖已在 FastAPI 镜像中（complaint-service 已用 redis.asyncio）。
- **数据库**：无需迁移——audit_logs 表已存在；users 表 status 字段已支持 "locked" 值；无新增表/列。
- **网关新增**：需为 admin/users 和 audit 两条新路径新增 `location` block（auth 前缀不挂 auth_request，admin 路径挂 auth_request）；audit 路径豁免（前端错误上报需先登录，走 auth_request 的 /api/v1/auth 体系）。

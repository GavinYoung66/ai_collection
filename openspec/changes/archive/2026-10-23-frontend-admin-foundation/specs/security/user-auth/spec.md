# Spec Delta

## Purpose

为管理台提供用户态认证端点契约——`POST /api/v1/auth/login` 用 bcrypt 校验用户名密码并签发 JWT，`GET /api/v1/auth/me` 返回当前登录用户的身份信息。两个端点由网关鉴权 sidecar（auth-sidecar）承载，复用 `common.security.rbac.create_token` 与 `common.db.models.user.User`。是前端管理台的登录入口，也是所有需鉴权前端页面的前置依赖。

## ADDED Requirements

### Requirement: 登录端点必须验证凭证且不可枚举用户
auth-sidecar MUST 暴露 `POST /api/v1/auth/login`，接收 JSON body `{username, password}`。MUST 按 `username` 精确查询 `users` 表；未命中或密码不匹配时返回 HTTP 401，信封 `code=B0002`、message 统一为"用户名或密码错误"，MUST NOT 区分用户名错误与密码错误（防枚举）。命中后 MUST 用 bcrypt 校验 `password` 与 `password_hash`；`status != active`（disabled / locked）时返回 HTTP **403**，信封 `code=B0003`、message="账号已停用或锁定"（与 SSOT §5.0 A.1 一致，非 401）。端点 MUST NOT 要求携带 Authorization 头。

#### Scenario: 密码错误返回统一 401 信封
- **WHEN** 请求 `POST /api/v1/auth/login` body 为 `{"username": "admin", "password": "wrong"}`
- **THEN** 返回 HTTP 401，响应体 `code=B0002`，`message` 为"用户名或密码错误"，无 token 字段

#### Scenario: 不存在的用户名返回 401
- **WHEN** 请求 `POST /api/v1/auth/login` body 为 `{"username": "nonexistent", "password": "anything"}`
- **THEN** 返回 HTTP 401，与密码错误的响应 body 完全一致（同 code、同 message），防用户名枚举

#### Scenario: 禁用状态用户拒绝登录
- **WHEN** 用户 `status=disabled`，请求正确密码
- **THEN** 返回 HTTP 403，信封 `code=B0003`，`message="账号已停用或锁定"`

#### Scenario: 锁定状态用户拒绝登录
- **WHEN** 用户 `status=locked`，请求正确密码
- **THEN** 返回 HTTP 403，信封 `code=B0003`（与 disabled 同码同文案）

#### Scenario: 空用户名或空密码参数校验
- **WHEN** body 缺少 username 或 password 字段，或字段为空字符串
- **THEN** 返回 HTTP 422，信封 `code=B0001`，`message="参数校验失败"`（无字段级明细），不触发 DB 查询

### Requirement: 登录成功必须签发 JWT 并返回统一信封
校验通过后 MUST 调用 `common.security.rbac.create_token(payload, expires_hours)` 签发 JWT：payload 固定含 `sub`（= `str(user.id)`）、`role`（取自 users.role，5 种规范值之一），`exp` 由 `create_token` 按 settings.JWT_EXPIRE_HOURS 写入（默认 24h）；`iss`/`aud` 仅在 settings.JWT_ISSUER / JWT_AUDIENCE 非空时写入（默认为空即跳过校验）。成功响应 MUST 为统一信封，成功码为 **int `0`**：

```jsonc
{ "code": 0, "message": "ok", "data": {
    "access_token": "<JWT>", "token_type": "bearer", "expires_in": 86400,
    "user": { "user_id": "<uuid>", "username": "admin", "real_name": "系统管理员", "role": "admin", "status": "active" }
  }, "trace_id": "<hex>" }
```

#### Scenario: 正确用户名密码登录成功
- **WHEN** 请求 `POST /api/v1/auth/login` body 为 `{"username": "admin", "password": "Admin@2026"}` 且 users 表存在该用户（`scripts/seed/seed_users.py` 种子账号）、status=active、password_hash 匹配
- **THEN** 返回 HTTP 200，响应体 `code=0`，`data.access_token` 非空字符串且可被 `verify_token_with_reason` 成功解析，`data.token_type="bearer"`，`data.expires_in` 为秒数，`data.user.username="admin"`，`data.user.role="admin"`

### Requirement: me 端点必须返回当前登录用户身份
auth-sidecar MUST 暴露 `GET /api/v1/auth/me` 端点。MUST 要求请求携带有效 `Authorization: Bearer <token>` 头；token 无效（签名错误、已过期、iss/aud 不匹配、缺失）时返回 HTTP 401（信封 `code=B0002`）。token 有效时 MUST 按 payload 的 `sub`（user_id）**查库复核**，用户不存在或已删除（`is_delete`）返回 HTTP **401** + `code=B0002`（message 与 token 无效同口径，均为「请先登录」）；`status != active` 返回 HTTP **403** + `code=B0003`；`role` MUST 取库内当前值（不用 token 内旧值），以支持「权限被回收但 token 未过期」的防御。返回统一信封 `{code: 0, message: "ok", data: user, trace_id}`，`user` 字段与 login 一致（`user_id/username/real_name/role/status`）。MUST NOT 返回 password_hash 或其他敏感字段。

#### Scenario: 有效 token 返回用户身份
- **WHEN** 请求 `GET /api/v1/auth/me` 携带 `Authorization: Bearer <有效 JWT>`，载荷 `sub=<user_id>`、`role=admin`，库内该用户 status=active、role=admin
- **THEN** 返回 HTTP 200，`code=0`，`data.user_id` 为库内 id，`data.username="admin"`，`data.role="admin"`，`data.status="active"`

#### Scenario: 库内角色已变更时返回新角色
- **WHEN** token 签发后库内该用户 role 由 `admin` 改为 `agent`，再调用 me
- **THEN** 返回 HTTP 200，`data.role="agent"`（不返回 token 内的旧 `admin`）

#### Scenario: 库内用户被禁用时返回 403
- **WHEN** token 有效但库内该用户 `status=disabled`
- **THEN** 返回 HTTP 403，`code=B0003`，`message="账号已停用或锁定"`

#### Scenario: 库内用户不存在或已删除时返回 401
- **WHEN** token 有效但库内已无该用户（或被软删除 `is_delete` 置位）
- **THEN** 返回 HTTP 401，`code=B0002`，`message="请先登录"`（与 token 无效同口径，不区分、不泄露「用户不存在」）

#### Scenario: 无 token 返回 401
- **WHEN** 请求 `GET /api/v1/auth/me` 不带 Authorization 头
- **THEN** 返回 HTTP 401，`code=B0002`

#### Scenario: 过期 token 返回 401
- **WHEN** 携带已过期 token
- **THEN** 返回 HTTP 401，`code=B0002`，`message="请先登录"`（内部 reason 不向客户端暴露）

#### Scenario: 伪造签名 token 返回 401
- **WHEN** 携带签名错误的 token
- **THEN** 返回 HTTP 401，`code=B0002`

### Requirement: auth-sidecar 必须具备 DB 连接能力
auth-sidecar 容器 MUST 能连接 PostgreSQL（与各业务服务共用 DB）。需要 volume 挂载 `common/` 以便 import SQLAlchemy Base + User model，并复用 `common.config.settings` 的 `PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD`（**不是 `POSTGRES_*`**；容器内 `PG_HOST=postgres`）。DB 连接 MUST 仅用于 login / me 两个端点的 users 表查询；`/internal/authz` MUST 保持无状态（不查 DB）。sidecar MUST 复用项目现有 DB 连接池模式，不引入第二套 DB 配置；DB 会话 MUST 懒加载，**DB 不可达时进程仍能启动**（不得连带打挂 `/internal/authz`）。新增依赖（`sqlalchemy[asyncio]` / `asyncpg` / `passlib[bcrypt]` / `bcrypt`）MUST 声明在 `deploy/auth/Dockerfile`（该服务无独立 requirements.txt）。

#### Scenario: auth-sidecar 容器能连接 DB
- **WHEN** docker-compose 启动 auth-sidecar 容器，env 含 `PG_HOST/PG_PORT/PG_DATABASE/PG_USER/PG_PASSWORD`
- **THEN** 容器内能成功执行 `SELECT 1` 或查询 users 表 count，无 connection refused / authentication failed 错误

#### Scenario: DB 不可达不阻塞启动
- **WHEN** PostgreSQL 未启动时启动 auth-sidecar
- **THEN** 进程仍正常启动，`/internal/authz` 仍可完成纯 token 校验；仅 login / me 返回统一信封 5xx

#### Scenario: login 端点成功查询 users 表
- **WHEN** 发送 login 请求，DB 正常且 users 表有种子用户
- **THEN** 端点正常查询并响应，无 DB 连接异常

#### Scenario: DB 不可用时 login 返回 502
- **WHEN** DB 挂了，发送 login 请求
- **THEN** 返回 HTTP 502（网关层 upstream 舱壁熔断）或 500（sidecar 自身），返回体为统一信封，不泄露 DB 错误细节

# Spec: backend-security-completion

**Change:** backend-security-completion
**Date:** 2026-10-27
**Input:** frontend-security-hardening REQ-5a/5b/5c 前端预留钩子 + 架构文档 §5.0 F complaint role 保护缺口 + P1 admin CRUD + 网关孤儿路由

## Scope

本 change 一次性补齐前序 `frontend-security-hardening` 在后端的 5 项依赖缺口：前端错误审计接口（REQ-5a）、登录失败 Redis 计数与账号锁定（REQ-5b）、Refresh Token 机制（REQ-5c）、P1 admin 用户 CRUD、网关孤儿路由清理。全部实现在既有服务内扩展（auth-sidecar 为主要承载者），不新建独立服务目录。

## Non-Goals

- 不改 complaint-service 复核/关闭端点的业务逻辑，仅 P1 用户 CRUD 接口本身带 role guard；complaint role protection 缺口列为后续独立 change
- 不实现前端 P1 系统管理页面（仅补后端接口）
- 不改既有 auth endpoints 的路径/信封/成功码；不引入新的 error code 编号段（仅 B0011 新增）
- 不迁移数据库 schema 或新建表（全部复用既有 AuditLog 与 User ORM）
- 不改 common/security/authn.py / rbac.py 逻辑（复用 create_token / verify_token_with_reason）

---

## REQ-1 前端错误审计接口（承接 REQ-5a）

**归属服务**：auth-sidecar（`deploy/auth/main.py`）
**网关路由**：`location /api/v1/audit`（挂 auth_request，需已认证）

### 端点契约

| 项 | 规范 |
|---|---|
| **Method** | POST |
| **Path** | `/api/v1/audit/frontend-error` |
| **鉴权** | 需 JWT（任意已认证角色）；auth_request 挂网关 location |
| **超时** | 12s（与 auth/login 一致） |

### 请求体

```jsonc
{
  "error_type": "TypeError",       // 必填；JS Error.name 或 "UnhandledRejection"
  "message": "...",                 // 必填；简短描述 < 500 字符
  "stack_trace": "...",             // 可选；完整 stack 字符串，< 2000 字符
  "url": "https://admin.example.com/complaints/c-001", // 必填；出错页面 URL
  "line": 120,                      // 可选；行号
  "col": 45,                        // 可选；列号
  "user_agent": "Mozilla/...",       // 可选；简化 UA 提取 < 300 字符
  "trace_id": "abc123..."           // 可选；前端透传 X-Trace-Id，无则服务端生成
}
```

```python
class FrontendErrorReport(BaseModel):
    error_type: str = Field(max_length=100)
    message: str = Field(max_length=500)
    stack_trace: str | None = Field(default=None, max_length=2000)
    url: str = Field(max_length=500)
    line: int | None = None
    col: int | None = None
    user_agent: str | None = Field(default=None, max_length=300)
    trace_id: str | None = Field(default=None, max_length=100)
```

### 成功响应

```jsonc
// HTTP 200
{ "code": 0, "message": "ok", "data": { "logged": true }, "trace_id": "..." }
```

### 错误响应

| HTTP | code | 场景 |
|------|------|------|
| 401 | B0002 | 未认证 / token 失效 |
| 422 | B0001 | 入参校验失败 |
| 500 | B0007 | 数据库写入异常（best-effort：失败写日志但返回 200？还是 500？→ 选返回 500，前端记录器不重试） |

### 数据落表

| 字段 | 写入值 |
|------|--------|
| `action` | `"frontend_error"` |
| `user_id` | 从 JWT sub 解析 |
| `detail` | JSONB，完整请求体 + trace_id |
| `ip_address` | `get_client_ip(request)` — 优先从 `X-Real-IP` 头取（网关已透传），缺失时回退 `request.client.host`；禁止直接写 `request.client.host`（Docker Compose 跨容器网络下返回 nginx 内部 IP，而非真实客户端 IP） |
| `trace_id` | 请求体 trace_id 或服务端生成 |
| `resource_type` | `"browser"`（固定值） |
| `resource_id` | 空 |

**不做**：不存 user_agent 全量到 resource_id（避免 IP + UA 隐私双积累）；IP 仅存 Inet 类型；error_type 归入 detail。

### 关联前端

- `frontend/src/shared/request.ts` 的 `registerErrorReporter(fn)` 已有空调度，需改为遍历调用并对每个 error 调用本接口
- `frontend/src/modules/complaint/components/TraceTimeline.vue` 的 safeLangsmithUrl 之外的错误（如 Vue warning、axios reject 非业务异常）也通过此通道上报

---

## REQ-2 登录失败 Redis 计数 + B0011 账号锁定（承接 REQ-5b）

**归属服务**：auth-sidecar（`deploy/auth/main.py`）
**前置**：`common/errors/codes.py` 新增 B0011

### 错误码新增

```python
# common/errors/codes.py
B0011 = "B0011"  # 账号已锁定
```
```python
# ERROR_MESSAGES 补
"B0011": "账号已锁定，请15分钟后重试",
```

### Redis Key 规范

| 项 | 值 |
|---|---|
| Key | `auth:login_fail:{username}` |
| Type | INCR + EXPIRE |
| 有效期 | 900s（15min） |
| 阈值 | 5 次 |
| 服务 | `common/resilience/_redis.get_redis()` |

### 登录流程变更

```
现有 login() 流程：
  1. 按 username 查 User
  2. bcrypt verify
  3. 校验 status=active
  4. create_token → 返回

新增前置步骤：
  0. 从 Redis 读 count（GET key）
     → count ≥ 5：查 User.status
       → status == "locked"：跳过后续，直接返回 HTTP 423 + B0011
       → status != "locked"（管理员解锁后残留计数）：
         INCR 可能 ≥ 5，仍置 locked 或清 key？
         → 策略：status="active" 时若 count ≥ 5，置为 "locked" 再返回 B0011

密码校验失败分支：
  a. 密码错误：INCR key；若结果 ≥ 5：置 User.status="locked" 并 EXPIRE 900s；返回 B0011（423）
     否则：返回 B0002（401，原逻辑）
  b. username 不存在：**不清零计数**（防枚举：每次不存在都 INCR，限制攻击者探测速度）
     → 返回 B0002（401），与密码错误同码，不暴露"用户是否存在"信息

成功登录：
  DEL key（成功登录清零计数）
```

### 关联前端

- `frontend/src/app/pages/Login.vue` 原预留 `else if (e.code === 'B0005')` → 改为 B0011
- `frontend/src/shared/utils/error-map.ts` 补 `"B0011": "账号已锁定，请15分钟后重试"`
- **complaint-service 的 B0005 完全不受影响**（语义"同工单重复提交"保持不变）

---

## REQ-3 Refresh Token 机制（承接 REQ-5c）

**归属服务**：auth-sidecar（`deploy/auth/main.py`）
**复用**：`common/security/rbac.py` 的 create_token（需扩展 type claim）

### Login 响应扩展

**现有 login 成功响应**：
```jsonc
{
  "access_token": "<JWT>",
  "token_type": "bearer",
  "expires_in": 86400,
  "user": { ... }
}
```

**新增 refresh_token**：
```jsonc
{
  "access_token": "<JWT>",
  "refresh_token": "<JWT>",   // 新增
  "token_type": "bearer",
  "expires_in": 86400,
  "refresh_expires_in": 604800, // 新增，7 天
  "user": { ... }
}
```

### JWT 载荷规范

| 类型 | claims | 有效期 |
|------|--------|--------|
| access_token | `sub`, `role`, `exp`, `type:"access"` | 24h（settings.JWT_EXPIRE_HOURS） |
| refresh_token | `sub`, `exp`, `type:"refresh"` | 7d（新增 JWT_REFRESH_HOURS=168） |

refresh_token 载荷**不含 role**（防止权限变更后旧 refresh_token 继续签发带过期 role 的 access_token）。

### 刷新端点

| 项 | 规范 |
|---|---|
| **Method** | POST |
| **Path** | `/api/v1/auth/refresh` |
| **鉴权** | Bearer 携带 refresh_token（不走 auth_request，由 auth-sidecar 自行校验） |
| **超时** | 12s |

### 刷新端点逻辑

```
1. 提取 Bearer token
2. verify_token_with_reason：
   - 失败（过期/篡改）：HTTP 401 + B0002 "refresh_token 无效或已过期"
   - payload.get("type") != "refresh"：HTTP 401 + B0002 "token 类型错误"
3. 查 User：payload["sub"] → UUID → db.get(User, user_id)
   - 不存在 / is_delete=="1" / status!="active"：HTTP 401 + B0002
4. 签发新 access_token（role 取 db 最新值）
5. 返回 {access_token, expires_in, token_type:"bearer"}
   （刷新端点**不返回新 refresh_token**——单 refresh_token 用满 7 天后用户重新登录，避免隐式无限续期）
```

### Redis 吊销机制（REQ-3.1 · 安全增强）

**为什么需要**：JWT 无状态，admin 重置密码 / 锁定账号后，旧 refresh_token 在 7 天有效期内仍可换 access_token。

**Redis Key**：`auth:refresh:{user_id}`（前缀 auth:，与 complaint-service 的 complaint: 严格隔离）

**四个操作点**：

| 场景 | 操作 | 代码位置 |
|------|------|---------|
| login 成功 | `SETEX auth:refresh:{user_id} 604800 "1"`（覆盖旧 key，保证同时仅一个有效 refresh_token） | auth/main.py login 成功分支 |
| refresh 调用 | 先 `EXISTS auth:refresh:{user_id}` — 不存在则返回 B0002 吊销错误；存在则 `DEL` 后签发新 access_token | auth/main.py refresh 端点 |
| admin 重置密码 | `DEL auth:refresh:{user_id}` — 吊销所有 refresh_token，强制下次登录重新 setex | auth/main.py admin_update |
| admin 锁定/停用 | `DEL auth:refresh:{user_id}` — 双保险（refresh 端点同时验 status==active） | auth/main.py admin_update |

**单轮使用模式**：refresh 一次后 DEL，新 refresh_token 必须走 login 重新 setex。比"单 7 天无限续期"更严格。

### 成功响应

```jsonc
// POST /api/v1/auth/refresh → HTTP 200
{
  "code": 0, "message": "ok",
  "data": { "access_token": "<JWT>", "expires_in": 86400, "token_type": "bearer" },
  "trace_id": "..."
}
```

### 错误响应

| HTTP | code | 场景 |
|------|------|------|
| 401 | B0002 | refresh_token 无效/过期/类型错误/用户不存在/被停用 |
| 422 | B0001 | Header 无 Bearer |
| 500 | B0007 | 签发异常 |

### 关联前端

- `frontend/src/app/stores/auth.ts`：login 响应多存 refresh_token；initFromStorage 多字段；clearAuth 清两个
- `frontend/src/shared/request.ts`：401 拦截分支前插入：若 auth store 有 refresh_token，调 refresh 端点获取新 access_token，然后重放原请求；refresh 也 401 则清态跳登录
- **这是从预留注释变为生效实现**——前端架构文档 §9.1 关于 401 处理的描述需同步更新（当前写"401 即清登录态重新登录"）

---

## REQ-4 P1 Admin 用户 CRUD

**归属服务**：auth-sidecar（`deploy/auth/main.py`）
**网关路由**：`location /api/v1/admin`（挂 auth_request，需已认证 + allowlist admin）

### 端点列表

| Method | Path | 鉴权 | 说明 |
|--------|------|------|------|
| GET | `/api/v1/admin/users` | admin | 列表（page/page_size） |
| POST | `/api/v1/admin/users` | admin | 创建用户 |
| PUT | `/api/v1/admin/users/{user_id}` | admin | 更新（可改 role/status/password 重置） |
| DELETE | `/api/v1/admin/users/{user_id}` | admin | 软删除（is_delete="1"） |

### 角色保护

auth-sidecar 无 role guard 基础设施——需新增一个极薄的 `_require_admin_roles` 依赖注入（检查 JWT payload role == "admin"）。**禁止**在 auth-sidecar 内重新发明角色比对逻辑——如有可能，抽 common 层。当前 auth-sidecar 最小实现：直接 `verify_token_with_reason` 拿到 payload["role"]，比对字符串。

### 请求/响应契约

#### GET /api/v1/admin/users

查询参数：`page`(default 1), `page_size`(default 20, max 100), `role`(可选过滤), `status`(可选过滤), `keyword`(可选 username/real_name 模糊)

```jsonc
// 响应 data
{
  "items": [
    { "user_id": "<uuid>", "username": "...", "real_name": "...",
      "role": "admin", "status": "active", "last_login_at": "2026-10-27T10:00:00Z" }
  ],
  "total": 42, "page": 1, "page_size": 20
}
```

#### POST /api/v1/admin/users

```jsonc
// 请求
{ "username": "alice01", "password": "Init@Temp123",
  "real_name": "Alice 张三", "role": "agent" }
```

约束：username 1~50 字符唯一；password ≥ 8 字符（auth-sidecar 仅做 min_length，复杂度由前端 rules 承担）；role 必须落在 5 种规范值内。

```jsonc
// 成功响应 data：新用户视图（无 password_hash）
{ "user_id": "<uuid>", "username": "alice01", "real_name": "Alice 张三",
  "role": "agent", "status": "active", "last_login_at": null }
```

#### PUT /api/v1/admin/users/{user_id}

```jsonc
// 请求（全可选；只传要改的字段）
{ "role": "qc_reviewer", "status": "disabled", "password": "NewPass@456" }
```

约束：role 变更必须落在 5 种规范值内；password 字段不为空时触发密码重置（auth-sidecar 重新 bcrypt 哈希覆盖）。不允许 admin 把自己 role 改成非 admin（防自杀：比对 `user_id != current_admin_id`）。

#### DELETE /api/v1/admin/users/{user_id}

软删除：`is_delete="1"`，不物理删除。不允许删除当前 admin 自己。

### 错误码复用

| HTTP | code | 场景 |
|------|------|------|
| 403 | B0003 | 非 admin 调用 |
| 404 | B0004 | user_id 不存在 |
| 409 | A5002 (complaint 风格？或新增 A9001) | username 重复 → 复用 complaint 的冲突码 |
| 422 | B0001 | role 不在允许列表 / 字段校验失败 |

---

## REQ-5 网关孤儿路由清理

**文件**：`deploy/gateway/server_common.conf.template`

### 删除内容

```nginx
# 删除（孤儿路由：后端真实路径已落在 /api/v1/complaints/*，此处未挂 rewrite）
location /api/v1/stats {
    proxy_pass http://${UPSTREAM_COMPLAINT};
}
location /api/v1/mode {
    proxy_pass http://${UPSTREAM_COMPLAINT};
}
```

### 验证

- 删除后 `grep 'location /api/v1/complaints' server_common.conf.template` 应保留
- 前端全部 API 调用均走 §5.0 B 表的 `/api/v1/complaints/stats` 与 `/api/v1/complaints/mode`（PUT），不影响任何功能
- 用例：curl `/api/v1/complaints/stats` 仍返回信封

### 相关架构文档更新

前端架构.md 注 1（§5.0 后）引用了这两条孤儿路由——删除后同步更新该注释为"已清理"。

---

## 统一规范

### 响应信封

全部新增端点严格遵守 `{code, message, data, trace_id}`，成功码 **int 0**（对齐 complaint / auth-sidecar 口径）。

### CORS / CSP

所有新路由前缀走 `/api/v1/audit/*` 与 `/api/v1/admin/*`，与现有 `/api/v1/auth/*` 共享 auth 前缀的 CORS 白名单（CORS_ALLOWED_ORIGINS）与 CSP connect-src（$CSP_CONNECT_SRC）。

### 数据库

- 无需迁移：audit_logs 与 users 表已存在，本 change 仅插入新数据行
- audit_logs.insert 失败时：error_report 返回 HTTP 500（B0007），不吞日志
- User 创建/更新的 bcrypt cost=12（与 seed_users.py 和 auth login 一致）

### 网关 location 新增（需同步修改 server_common.conf.template）

auth-sidecar 的 upstream 在网关中已硬编码为 `proxy_pass http://auth;`（见 /api/v1/auth 现有 location，docker-compose 服务名 `auth`），本 change 新增的两条 location 也跟随此硬编码模式，不引入新的 envsubst 变量（UPSTREAM_AUTH 不存在且无需新增）：

```nginx
# --- REQ-5a 前端错误审计（auth-sidecar 扩展） ---
location /api/v1/audit {
    include /etc/nginx/auth_request.conf;
    proxy_pass http://auth;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Trace-Id $trace_id;
    proxy_connect_timeout 3s;
    proxy_read_timeout 15s;
    limit_req zone=gateway burst=40 nodelay;
}

# --- REQ-4 admin users（auth-sidecar 扩展） ---
location /api/v1/admin {
    include /etc/nginx/auth_request.conf;
    proxy_pass http://auth;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Trace-Id $trace_id;
    proxy_connect_timeout 3s;
    proxy_read_timeout 15s;
    limit_req zone=gateway burst=40 nodelay;
}
```

注：audit 和 admin location 都挂 `auth_request.conf`（include 复用现有 auth_request + auth_request_set 指令），与 /api/v1/auth 不同（auth/login 必须豁免 auth_request，否则死锁）。两条 location 与 /api/v1/auth 共享同一 upstream（auth-sidecar 容器），auth-sidecar FastAPI app 直接注册多组路由（/internal/authz + /api/v1/auth/* + /api/v1/audit/* + /api/v1/admin/*），不做进程拆分。

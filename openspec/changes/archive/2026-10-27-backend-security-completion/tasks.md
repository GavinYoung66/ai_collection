# Tasks — backend-security-completion

**Schema:** spec-driven
**Change:** backend-security-completion
**Specs:** specs/backend-security/spec.md

## 执行批次

### 批次 A：基础设施前置 — B0011 + RBAC 扩展

- [x] A1. `common/errors/codes.py` 新增 `B0011 = "B0011"` comment "账号已锁定"
- [x] A2. `common/errors/codes.py` ERROR_MESSAGES 补 `"B0011": "账号已锁定，请15分钟后重试"`
- [x] A3. `common/security/rbac.py` 的 create_token 扩展 type claim（access/refresh）+ 新增 create_refresh_token；verify_token_with_reason 校验 type
- [x] A4. settings.py 补 JWT_REFRESH_HOURS=168
- [x] A5. 前端对齐：Login.vue `B0005 → B0011`；error-map.ts 补 B0011 映射；无其他 B0005 误引用

### 批次 B：REQ-5b 登录失败 Redis 计数 + B0011

- [x] B1. auth-sidecar main.py login() 流程前置 Redis 计数器（key `auth:login_fail:{username}`，TTL 900s，阈值 5）
- [x] B2. 密码错误 → INCR；≥5 置 User.status="locked" → 返回 HTTP 423 + B0011
- [x] B3. username 不存在 → 不清零计数（防枚举），返回 B0002（与密码错误同码）
- [x] B4. 成功登录 → DEL key（清零）
- [x] B5. 锁定状态查询：count ≥ 5 且 status="locked" 时，423 + B0011；管理员手动解锁后 count 仍 ≥ 5 但 status="active"，仍置 locked（防绕过）

### 批次 C：REQ-5c Refresh Token

- [x] C1. create_refresh_token(sub) → `{"sub":..., "exp":..., "type":"refresh"}`
- [x] C2. login 成功响应新增 `refresh_token` + `refresh_expires_in` 字段
- [x] C3. 新增 `POST /api/v1/auth/refresh` 端点：校验 type=="refresh" → 查 User → 签发新 access_token → 返回
- [x] C4. refresh 端点 401 分支：token 类型错误 / 过期 / 用户不存在或停用 → 统一 B0002
- [x] C5. refresh 端点**不签发新 refresh_token**（单轮 7 天自然过期）+ Redis SETEX/EXISTS+DEL 吊销机制

### 批次 D：REQ-5a 前端错误审计接口

- [x] D1. 新增 `POST /api/v1/audit/frontend-error` 端点（FastAPI POST + Pydantic schema FrontendErrorReport）
- [x] D2. 端点：JWT 鉴权、写入 AuditLog（action="frontend_error"，detail JSONB）
- [x] D3. IP 地址改用 `get_client_ip(request)` → 优先 X-Real-IP（替换原 request.client.host）
- [x] D4. 前端 request.ts registerErrorReporter 改为遍历调用 + main.ts 三事件桥接（window.error / unhandledrejection / axios 非零码）+ fire-and-forget

### 批次 E：REQ-4 P1 Admin 用户 CRUD

- [x] E1. auth-sidecar 新增 _require_admin 依赖注入（校验 JWT payload role=="admin"）
- [x] E2. 新增 GET /api/v1/admin/users（列表 page/page_size + role/status/keyword 过滤）
- [x] E3. 新增 POST /api/v1/admin/users（创建：bcrypt cost=12，role 5 种规范值校验，username 唯一）
- [x] E4. 新增 PUT /api/v1/admin/users/{user_id}（更新 role/status/password 重置；禁止自改 role）
- [x] E5. 新增 DELETE /api/v1/admin/users/{user_id}（软删除 is_delete="1"；禁止自杀）
- [x] E6. 创建/更新响应：不返回 password_hash

### 批次 F：REQ-5 网关孤儿路由清理 + location 新增

- [x] F1. server_common.conf.template 删除 `location /api/v1/stats` 与 `location /api/v1/mode` 两个孤儿路由
- [x] F2. 新增 `location /api/v1/audit`（挂 auth_request，proxy_pass http://auth）
- [x] F3. 新增 `location /api/v1/admin`（挂 auth_request，proxy_pass http://auth）
- [ ] F4. 架构文档 §5.0 注 1 更新孤儿路由说明为"已清理"（后续文档同步）

### 批次 G：前端联动（从预留到生效）

- [x] G1. auth.ts refresh_token 字段从 null 默认改为 login 响应真实值存储；initFromStorage / clearAuth 同步
- [x] G2. request.ts 401 拦截分支前置 refresh 逻辑：有 refresh_token → 调 refresh → 重放原请求 → 清态跳登录
- [x] G3. registerErrorReporter 从空调度改为实际遍历调用 audit 端点 + main.ts 全局错误桥接
- [x] G4. Login.vue B0005 → B0011；error-map.ts 补 B0011

### 批次 H：单元测试

- [ ] H1. B0011 Redis 计数单测（INCR 流程、阈值触发、TTL、成功登录清零）
- [ ] H2. Refresh Token 单测（type 校验、7 天过期、单轮不续期）
- [ ] H3. Audit 端点单测（写入 AuditLog、IP/trace_id 透传、auth 拒绝）
- [ ] H4. Admin CRUD 单测（role guard、username 唯一、禁止自杀、软删除）
- [ ] H5. 前端 vitest：auth store refresh_token 存取 / request 401 后刷新重试

### 批次 I：集成验证

- [ ] I1. docker compose 启全量：auth-sidecar + complaint + qc + rag + gateway + Redis + Postgres
- [ ] I2. curl login 成功 → 拿 refresh_token → curl refresh 换 access_token → 用新 token 调 /me
- [ ] I3. 密码错 5 次 → login 返回 423 + B0011 → Redis key 存在 → User.status="locked"
- [ ] I4. admin 用户 CRUD：创建 agent → 改 role → 软删除 → 确认 complaint-service B0005 不受影响（同工单重复提交校验仍可用）
- [ ] I5. 前端浏览器真实链路：登录 → 构造错误 → 看 audit_logs 是否新增行
- [ ] I6. 孤儿路由清理后：curl /api/v1/complaints/stats 仍返回信封；curl /api/v1/stats 返回 404（Nginx 默认）
- [ ] I7. vitest 前端全量 39 文件 349 用例 + 新增 refresh/auth 用例通过

### 全量交叉审核

- [ ] X1. OpenSpec 规范 ↔ 实现代码：逐条 REQ 核对字段/路径/错误码
- [ ] X2. 错误码交叉：B0005(complaint) 与 B0011(auth) 不重叠；complaint/service 的所有错误码未被篡改
- [ ] X3. Redis key 命名与 complaint-service 的 redis_keys.py 不冲突（前缀 auth: vs complaint:）
- [ ] X4. 网关 location 增删无遗漏、无冲突、proxy_pass 均指向 UPSTREAM_AUTH
- [ ] X5. 数据库 schema 无新增/变更（确认无迁移脚本误写）
- [ ] X6. docker-entrypoint.sh 确认 UPSTREAM_AUTH 变量已 export 并在 VARS 白名单
- [ ] X7. openspec archive backend-security-completion

## 验收标准

1. **REQ-5a**：audit/frontend-error 端点写入 audit_logs（action="frontend_error"），JWT 鉴权生效，422 拒入参错
2. **REQ-5b**：密码错 5 次后返回 423+B0011，User.status=locked，15min 后 Redis key 过期自动可再试，成功登录清零计数
3. **REQ-5c**：login 响应新增 refresh_token（type="refresh" claim），refresh 端点签发新 access_token（7 天单轮），type 校验拦截伪造
4. **REQ-4**：admin CRUD 4 端点 role guard {"admin"} 生效，role 5 种规范值校验，禁止自杀/自改 role，软删除
5. **REQ-5**：孤儿路由删除后 `/api/v1/complaints/stats` 仍返回信封；audit + admin 新 location proxy 到 UPSTREAM_AUTH
6. **complaint-service 零影响**：B0005 同工单重复提交逻辑不变；所有 review/close/disposition 端点正常
7. **vitest 前端全绿**：39 文件 349 用例 + 新增 refresh/auth 用例
8. **npm audit 0 高危**

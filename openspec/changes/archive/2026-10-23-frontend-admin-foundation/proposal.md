# Proposal

## Why

当前三个业务服务各自托管一个演示页（complaint-service/web、qc-service/web、rag-service 的 Gradio），彼此割裂无统一入口，且完全缺失鉴权——任何人可直接访问后端接口。没有可复用的前端底座，后续四业务模块（智能投诉/合规质检/RAG 知识库/LLM 用量）每做一个都得从工程初始化、请求封装、布局骨架重抄一遍。F0 先立底座+登录，后续 F1–F4 直接长进去。

## What Changes

- **新建** `frontend/` Vue 3 管理台工程（Vite + TypeScript + Pinia + Vue Router + Element Plus），与 services/ 平级
- **auth-sidecar 扩容用户态端点**：`POST /api/v1/auth/login`（bcrypt 校验 + JWT 签发，返回 `access_token` 与 `user` 对象）、`GET /api/v1/auth/me`（按 JWT `sub` 查库返回当前用户对象），新增 DB 连接能力
- **nginx 网关新增 auth 路由**：`/api/v1/auth/login` 和 `/api/v1/auth/me` 反代到 auth-sidecar，豁免 ENABLE_AUTH（登录是获取 token 的入口）
- **前端底座**：axios 唯一实例（信封解包/Bearer 注入/401 跳登录/trace_id 透出）、Pinia auth store、路由守卫、AdminLayout（侧边栏角色过滤+顶栏+面包屑+`<router-view>`）、登录页
- **五模块占位路由**：智能投诉/合规质检/RAG 知识库/LLM 用量/系统管理 五个模块各一个"建设中"页面占位，`meta.roles` 声明角色可见性（矩阵见 SSOT §3.2）
- **CI 增量**：`.github/workflows/ci.yml` 新增 `frontend` job（lint + typecheck + vitest），纳入现有门禁（`common` + `service-test` 5 服务矩阵）

## Capabilities

### New Capabilities

- `security/user-auth`: 用户态认证端点契约——`POST /api/v1/auth/login`（bcrypt 校验 + JWT 签发）、`GET /api/v1/auth/me`（返回当前用户身份），auth-sidecar 承载，复用 `common.security.rbac.create_token` 与 `common.db.models.user.User`
- `frontend/admin-foundation`: Vue 3 管理台底座能力——工程初始化、axios 信封解包、auth store + 路由守卫、AdminLayout 骨架、角色菜单过滤、登录页、模块占位路由

### Modified Capabilities

- `platform/api-gateway`: 新增 `/api/v1/auth/login` 与 `/api/v1/auth/me` 路由反代 auth-sidecar，两个端点豁免网关鉴权（ENABLE_AUTH=true 时也必须允许未登录访问）

## Non-Goals（本期明确不做）

- 四个业务模块的功能实现（F1 智能投诉、F2 质检+RAG、F3 LLM 用量，各期独立 change）
- LLM 用量查询 API（F3 新增 `/api/v1/llm-usage/*` 端点）
- 开放注册、用户管理 CRUD（P1 再议）
- Nginx `/admin/` 生产托管 + `ENABLE_AUTH=true` 打开（F4 收口）
- ECharts（F3 LLM 用量看板时再装）、移动端适配、UI 主题定制
- 旧演示页下线（F4 收口时处理）
- SSE 统一封装（后端尚无 SSE 端点，F1 研判走状态轮询）
- 前端异步任务注册表 / 长任务管理器（F1 研判为同步阻塞调用，网关 judge location 120s 兜底）
- 前端埋点、异常上报底座、统一 storage 管理层（无业务价值验证前不引入）
- Schema 动态表单引擎（各模块表单手写，不抽象）
- 运行时插件注册（`install(app)` / `router.addRoute` / 动态 import 按需加载）、微前端 / 模块联邦、代码生成器（可插拔定义为编译期：删目录 + 删装配一行）
- 统一 AI 通用组件层 / ai-components（F0 只收敛 `shared/components/base` 三个基础组件）

## Impact

### 新增文件

| 位置 | 说明 |
|---|---|
| `frontend/` 整个目录 | Vue 3 工程（package.json / vite.config.ts / tsconfig.json / src/…）。**注**：该目录已存在且含 `前端架构.md`（SSOT），脚手架必须用临时目录生成后合入，禁止对 `frontend/` 直接 `npm create vite` |

### 修改文件

| 位置 | 变更 |
|---|---|
| `deploy/auth/main.py` | 扩展 login / me 端点 + DB session 依赖（懒加载，不阻塞启动） |
| `deploy/auth/Dockerfile` | 新增 `sqlalchemy[asyncio]` / `asyncpg` / `passlib[bcrypt]` / `bcrypt` 依赖（该服务无独立 requirements.txt） |
| `deploy/gateway/server_common.conf.template` | 已新增 `location /api/v1/auth` 反代 auth-sidecar；**故意不 include `auth_request.conf`**（登录入口需豁免鉴权） |
| `deploy/gateway/nginx.conf.template` | 更新 auth upstream 注释（两条用途：`/internal/authz` + `/api/v1/auth`） |
| `scripts/seed/seed_users.py` | 已增补 `qc_reviewer01`（`Qc@2026`）、`trainer01`（`Train@2026`），共 10 账号 / 5 角色，bcrypt rounds=12 |
| `docker-compose.gateway.yml` | 已为 auth-sidecar 增加 `PG_HOST=postgres` 与 `default` 网络（与 postgres 同网） |
| `.github/workflows/ci.yml` | 新增 frontend job（不动现有 `common` 与 `service-test` 5 服务矩阵） |

### 后端影响

- **auth-sidecar**：新增 DB 连接池（查 users 表），但**不修改**现有 `/internal/authz` 端点（保持纯 token 校验无状态）
- **种子脚本**：`scripts/seed/seed_users.py` 增补 `qc_reviewer01`、`trainer01` 两个账号，使 F0「按角色见菜单」可覆盖后端 5 种角色；不改表结构、不做迁移
- **nginx 网关**：路由表新增 1 条 `/api/v1/auth` 前缀 location（覆盖 login/me）；auth 豁免清单扩展
- **qc-service**：仅修复角色词表短码（`require_roles` 统一规范值 + 大小写不敏感），无接口/响应契约变更；其余业务服务零改动

### API 新增（无破坏性）

- `POST /api/v1/auth/login` — body `{username, password}`；成功 `{code: 0, data: {access_token, token_type: "bearer", expires_in, user: {user_id, username, real_name, role, status}}}`；凭证错误 401 `B0002`，账号停用/锁定 403 `B0003`，入参校验 422 `B0001`
- `GET /api/v1/auth/me` — header `Authorization: Bearer <token>`，返回 `{code: 0, data: user}`（同上 `user` 对象，role 取库内当前值）

### CI 影响

- 新增 `frontend` 依赖 job（现有 2 个 job：`common` + `service-test` 5 服务矩阵，保持不变）
- 现有 `protect-master` ruleset 需把 frontend job 加入 required checks（不影响现有 `common` 与 `service-test`）

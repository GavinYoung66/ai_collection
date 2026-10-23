# Design — frontend-admin-foundation

## Context

本变更落地 F0 期前端底座 + 登录。**唯一权威设计源是 `frontend/前端架构.md` v1.2（SSOT 冻结）**，本文档是它的派生实现决策，不引入任何与 SSOT 冲突的口径。冲突时按 SSOT 元规则 ④ 修订本文档。

后端事实（已逐条源码核验，不得改写）：

- 统一信封 `{code, message, data, trace_id}`；API 前缀 `/api/v1`；网关 `proxy_pass` 不带 URI，路径原样透传。
- 错误码实测：`B0002` 未认证（`@auth_required`）、`B0006` 限流（`@rate_limited` 429）、`B0008` 网关错误（502）、`B0009` 服务暂不可用（504）。
- JWT：`common/security/rbac.py` 的 `create_token(data, expires_hours=None)` / `verify_token_with_reason(token)`；载荷固定 `sub`=user_id、`role`=规范角色值、`exp`（由 `create_token` 注入），`JWT_ISSUER` / `JWT_AUDIENCE` 为空时跳过校验。
- 用户模型：`common/db/models/user.py`，字段 `username / password_hash(bcrypt cost=12) / real_name / role / status(active|disabled|locked) / last_login_at`；**5 种角色以此文件为唯一来源**。
- 种子账号：`scripts/seed/seed_users.py` 已含 10 个账号 / 5 种角色（admin、supervisor01/02、agent01~05、qc_reviewer01 / `Qc@2026`、trainer01 / `Train@2026`），F0 可直接实测全矩阵。
- 网关超时：`deploy/gateway/server_common.conf.template` 普通 15s、`/api/v1/complaints/{id}/judge` 专用 location 120s。
- 鉴权开关：`ENABLE_AUTH` 默认 false，`auth_request.conf` 由 entrypoint 按开关生成；**`location /api/v1/auth` 已落地**（`server_common.conf.template`），原样透传 auth-sidecar，且**故意不 include `auth_request.conf`**（否则登录需先登录，死锁）。
- trace：`common/trace/middleware.py` — 请求带 `X-Trace-Id` 则沿用，无则生成 uuid，并在响应头回传。

## Goals / Non-Goals

**Goals（F0）**

1. `frontend/` 独立工程初始化（Vue 3 + TS + Vite + Pinia + Router + Element Plus）。
2. 登录链路：`POST /api/v1/auth/login`、`GET /api/v1/auth/me` + 前端 token 管理与登录页。
3. 主布局骨架：侧边栏（角色过滤菜单）+ 顶栏（用户/角色/登出）+ 面包屑 + `<router-view>`。
4. 全局能力：唯一 axios 实例（Bearer 注入 / 信封解包 / 401 跳登录 / TIMEOUT 常量表 / 错误码白名单）、auth store、路由守卫、模块契约与边界工具链。
5. 五模块占位路由（智能投诉 / 合规质检 / RAG 知识库 / LLM 用量 / 系统管理），仅显示「建设中」。

**Non-Goals（本变更不做，取自 SSOT §18）**

- 任何业务模块的功能实现（F1–F4、P1 各期独立 change）。
- LLM 用量查询 API（F3）。
- 开放注册、用户管理 CRUD（P1）。
- Nginx `/admin/` 生产托管、`ENABLE_AUTH=true` 开启（F4 收口）。
- **SSE 统一封装、前端异步任务注册表、前端埋点/异常上报底座、统一 storage 管理层、Schema 动态表单引擎**（SSOT §18 明确不做）。
- 运行时插件注册（`install(app)` / `router.addRoute`）、微前端 / 模块联邦。
- ECharts、移动端适配、UI 主题定制、TypeScript 代码生成器。
- 旧演示页下线。

## Decisions

### D1: 三层分层（app 壳 / shared 公共能力 / modules 业务模块）

**决策**：按 SSOT §7 采用三层，单向依赖 `app → modules → shared`：

```
app/       底座壳：main.ts、router（唯一装配点）、layouts、pages、stores、styles —— 不含业务页面
shared/    公共能力：request、types、components/base、composables、utils —— 无业务语义
modules/   业务模块：每模块自包含 index/routes/api/types/store/views/components —— 只依赖 shared
```

铁律（depcruise + ESLint 强制，见 D9）：

1. `modules/A` 不得 import `modules/B`（含类型、组件、store）。
2. `modules/*` 只依赖 `shared/` 与第三方库，不得 import `app/`。
3. `shared/` 不得 import `modules/` 或 `app/`；不得出现业务名词（complaint/qc/rag）。
4. `app/router/index.ts` 是唯一装配点；`app/` 其他文件不得 import 模块内部文件。
5. 每模块 `api.ts` 是访问后端唯一入口，只能调 `shared/request`；禁止 `fetch`、禁止新建 axios 实例。
6. `src/mocks/`（MSW）仅开发态，只有 `app/main.ts` 可按 `import.meta.env.DEV` 条件引入；其余任何文件（含 `shared/`、`modules/*/api.ts`）不得 import `mocks/`，生产构建不得包含 mock。

**理由**：SSOT 已定；与后端「一个服务一个目录」同构，删除模块 = 删目录 + 删装配一行，无孤儿残留。

**备选（放弃）**：按技术分层（全局 `src/api/`、`src/router/modules/`）—— 删模块会残留 api/路由孤儿文件，破坏可插拔。

### D2: 模块契约 `BizModule`（编译期可插拔）

**决策**：每个模块 `modules/<biz>/index.ts` 导出唯一出口：

```ts
export interface BizModule {
  name: 'complaint' | 'qc' | 'rag' | 'usage' | 'system'
  routes: RouteRecordRaw[]   // 每条必须带 meta.title / icon / roles
  setup?: () => void         // 可选，极少用
}
```

目录固定：`index.ts / routes.ts / api.ts / types.ts / store.ts(可选) / views/ / components/`。

**理由**：SSOT §7.1 已定；这使「可插拔」有机器可验证判据——删除目录 + 删装配行后 `vue-tsc --noEmit && vite build` 仍通过。

### D3: 路由装配为编译期静态 import，不做运行时注册

**决策**：`app/router/index.ts` 静态 import 各模块 `index.ts`，汇总 `BizModule.routes` 后一次性 `createRouter`。**不采用** `router.addRoute` 动态注册、不采用 `install(app)` 插件体系、不做运行时按需加载开关。

**理由**：SSOT §11 取舍说明——模块数 ≤6，运行时插拔属过度工程；静态 import 换取完整类型提示与最简装配。**本项目的「可插拔」定义 = 编译期可增删，不是运行时热插拔**。

**备选（放弃）**：`install(app)` + `registerRoute` + 动态 import 按需加载 —— 与 SSOT §11 冲突，且会丢类型提示。

### D4: `meta.roles` 权限模型与守卫顺序

**决策**：路由 `meta.roles?: Role[]` 声明可见角色，缺省 = 登录即可见；`meta.title/icon` 驱动侧边栏与面包屑。全局前置守卫（SSOT §11/§11.1）：

1. 目标为 `/login`（及 `/403`）→ 直接放行，不触发 `ensureLoaded`；
2. `await auth.ensureLoaded()`：`loaded === true` 直接返回；否则复用同一个 in-flight Promise 只发一次 `/me`，失败清态并抛出；
3. 仍无 token / userinfo → `/login?redirect=<目标完整路径>`；
4. 已登录但 `role` 为空 → 不计算重定向，落 `/403`；`role` 不在 `meta.roles` → `/403`；
5. 其余放行。

登录成功后的落点：`redirect` 参数存在则回跳（仅接受站内相对路径）；否则按 **§3.2 矩阵求「当前角色可访问的第一个模块路由」**，无可访问路由时落 `/403`。

首路由映射（路径取自 SSOT §11.1，F0 占位页同路径）：`admin` / `supervisor` / `agent` → `/complaints`；`qc_reviewer` → `/qc/tasks`；`ai_trainer` → `/kb/documents`；`admin` 亦可进入 `/usage`、`/system/users`，但按表中首条取 `/complaints`。**禁止写死 `/complaints` 给所有角色**。

**角色 × 模块矩阵（唯一事实源，落地为各模块 `meta.roles`）**

| 模块 | admin | supervisor | agent | qc_reviewer | ai_trainer |
|---|---|---|---|---|---|
| 智能投诉 | ✓ | ✓ | ✓ | | |
| 合规质检 | ✓ | ✓ | | ✓ | |
| RAG 知识库 | ✓ | | | | ✓ |
| LLM 用量 | ✓ | ✓ | | | |
| 系统管理 | ✓ | | | | |

**理由**：`redirect` 与角色矩阵必须组合使用——给所有角色写死 `/complaints` 会让 qc_reviewer / ai_trainer 登录即 403 死循环。菜单过滤与路由拦截共用同一份 `meta.roles`，消除「菜单藏了但 URL 能进」。

### D5: 唯一 axios 实例与信封解包

**决策**：`shared/request.ts` 是全工程唯一 axios 实例。

- 请求拦截器：读 `request.ts` 模块级 token holder（由 auth store 在 `initFromStorage / login / logout` 时通过 `setAuthToken()` 写入）；**`shared/` 不得 import `app/stores`**（否则形成 `store → api → request → store` 循环，且违反 D1 铁律 3）；**auth store 是唯一 localStorage 读写点（key `ac_token`），`request.ts` 只持内存 token、不读写存储**；**token 为空不注入**（禁止 `Bearer null`）；`X-Trace-Id` 按 D7 处理。
- HTTP 429 或 `code === "B0006"`：读响应头 `Retry-After` 计算退避时长并提示；**拦截器不自动重试**，重试只由调用方（`usePolling` / 按钮）对幂等 GET 执行一次。
- 响应拦截器：**所有 HTTP 状态码统一按信封解析**（网关 401/429/502/504 也返回信封）：
  0. **非信封白名单例外**：仅 `GET /api/v1/sensitive-words/current.dict` 走 `responseType:'text'`，直接返回字符串、跳过信封解析，其 404 映射为「词典为空」空态（SSOT §5.0.1）；
  1. body 是信封 → 走业务分支；不是信封（超时/连接中断/HTML 404）→ 兜底提示，trace 显示「无」；
  2. `isSuccess(code)`（`0` 或 `"0"`）→ 解包返回 `data`；
  3. HTTP 401 或 `code === "B0002"` → 清 auth → `/login?redirect=<当前路径>`；**例外：`POST /api/v1/auth/login` 自身的 401 只做表单提示，不清态、不跳转**；
  4. 其他非成功 code → 查 `shared/utils/error-map.ts` 白名单提示（附 trace_id 后 8 位）→ reject。
- 业务 API 函数返回强类型 `data`，**业务代码永远见不到信封**。

**理由**：网关四种错误也走信封，若只按 HTTP 2xx 分支会在 401/429 场景漏解析。

**F0 联调补充决策（批次 5 回填）**：`axios.baseURL` 取 `VITE_API_BASE`，且**固定为空字符串**（`.env.development` / `.env.production` 均为空）。api 层路径一律按 SSOT §5.0 契约基线写根绝对路径 `/api/v1/...`，空 baseURL 下即为同源 `/api/v1/...`，开发态由 vite proxy 把 `/api` 转发到网关 8080，生产态同源直连。**禁止**改成 `baseURL=/api` 却保留路径里的 `/api` 前缀（会拼出 `/api/api/v1/...`，登录必失败）。二者必须成对修改：若回退 `baseURL=/api`，则路径要改为 `/v1/...`，并且 `request.ts` 识别「登录自身 401」的匹配串（`includes('/api/v1/auth/login')`）必须同步调整，否则登录失败会误触发全局清态跳转，形成登录页自跳死循环。SSOT §9.1/§9.2 已同步修订为本口径。

### D6: TIMEOUT 常量表（单实例不派生）

**决策**：`shared/request.ts` 内维护常量表，api 层按端点引用：

| 常量 | 值 | 对应网关 |
|---|---|---|
| `normal` | 12 000 ms | 15s（留 3s 余量，前端先失败） |
| `judge` | 110 000 ms | 120s（judge 专用 location） |

**F0 不设 `upload` 常量**：后端放宽上传超时（≥60s）前，上传沿用 `normal`；放宽后新增 `upload: 50_000` 并回填本表与 SSOT 附录 B.2（SSOT §9.3）。

**不派生第二 axios 实例**（长任务 / 上传复用同一实例 + 不同 timeout）。铁律：前端超时 < 网关 `proxy_read_timeout`。

**理由**：SSOT §9.3；双实例会让拦截器/信封/错误处理出现第二份，必然漂移。

### D7: `X-Trace-Id` F0 仅留扩展点，不启用

**决策**：F0 不生成、不注入 `X-Trace-Id`（由后端 middleware 生成 uuid），仅在 `shared/request.ts` 预留模块级 trace 上下文读写函数；拦截器**响应侧**统一从响应头 `X-Trace-Id` 与信封 `trace_id` 取值（优先响应头），用于错误提示后 8 位。F1 投诉详情页落地「复合链路」写入。

**理由**：SSOT §9.4——后端已具备生成能力，F0 前端再生成会形成双来源；且 F0 无需要串联的复合链路。

### D8: auth store（token 持久化，user/role 不持久化）

**决策**：

```ts
interface CurrentUser {
  user_id: string
  username: string
  real_name: string
  role: Role
  status: 'active' | 'disabled' | 'locked'
}

interface AuthState {
  token: string | null      // 唯一被持久化的字段 → localStorage（key `ac_token`，走 STORAGE_KEYS.TOKEN 常量）
  user: CurrentUser | null  // 不持久化，/me 重建
  role: Role | null         // 不持久化，取自 user.role
  loaded: boolean           // 首屏是否已完成 /me 引导
}
// actions: initFromStorage() / login(u,p) / logout() / fetchMe() / ensureLoaded() / hasRole(...roles)
```

初始化时序：`setup()` 先 `initFromStorage()`，**有 token 才** `fetchMe()` 重建 user/role；`ensureLoaded()` 在 `loaded === true` 时直接返回，否则复用同一个 in-flight Promise（并发路由跳转只发一次 `/me`），失败时清空登录态并抛出，由守卫统一跳登录。token 过期时 `fetchMe` 触发 401，由拦截器清空并跳登录。`logout()` 清四字段 + localStorage 并跳 `/login`。

**持久化选 localStorage 而非 sessionStorage**：跨会话保持登录（SSOT §10）；安全性由 24h JWT 过期 + `fetchMe` 服务端重建角色兜底（防「权限已回收但 token 未过期」）。

### D9: 登录端点归属 — 扩容 auth-sidecar

**决策**：`POST /api/v1/auth/login`、`GET /api/v1/auth/me` 落在现有 `deploy/auth/main.py`（auth-sidecar），新增 DB 会话依赖读 `users` 表；现有 `/internal/authz` **保持不变**（仍为无状态纯 token 校验）。网关新增两个 location 反代 auth，并**豁免** `auth_request`（login 是获取 token 的入口，开启 ENABLE_AUTH 后也必须可达）。

- login：按 `username` 查库 → bcrypt 校验；用户不存在 / 密码错误 **统一返回 401 + `B0002` + 同一条文案**（防枚举）；`status != active` 返回 **403 + `B0003`**「账号已停用或锁定」；入参校验失败 422 + `B0001`。成功调用 `create_token({"sub": str(user.id), "role": user.role})`，返回 `data: {access_token, token_type: "bearer", expires_in, user: {user_id, username, real_name, role, status}}`（成功码 int `0`）。
- me：`verify_token_with_reason` → 失败 401（内部区分过期/签名/iss/aud，**不向客户端暴露 reason 字段**）；payload 合法后 MUST 按 `sub`（user_id）查库复核，用户不存在或已删除 → **401 + `B0002`**，`status != active` → **403 + `B0003`**，`role` **取库值而非 token 内旧值**（SSOT §10 要求 `fetchMe()` 防御「权限被回收但 token 未过期」，故必须查库）；返回 `data` 为单个 `user` 对象（字段同 login）。
- 账号枚举防护：用户不存在与密码错误返回**同一条** `B0002` 文案；停用账号返回独立的 `B0003`（SSOT §5.0 A.1 已冻结）。
- 契约唯一依据：SSOT `§5.0 A.1 认证契约`（v1.2）；前端禁止解析 JWT，角色/过期一律以 `/me` 与 401/403 为准。

**理由**：auth-sidecar 已挂载 `common/`，是唯一同时具备 JWT 能力与网关内网位置的组件；放业务服务会让 6 个服务都依赖用户表。

**备选（放弃）**：放 complaint-service（与投诉无耦合，且会引入跨服务依赖）。

### D10: Vite 配置

**决策**：`base: '/admin/'`（F0 起固定，开发态入口即 `http://localhost:5173/admin/`；F4 只改托管，不再改构建配置）；**base 三处必须同源**——`vite.config.ts` 的 `base`、路由 `createRouter({ history: createWebHistory(import.meta.env.BASE_URL) })`、Nginx 托管前缀 `/admin/`（漏 router history 会让深链刷新 404，属 F0 验收项）；`resolve.alias` `@ → src`；`server.proxy` `/api → http://localhost:8080`（仅开发态，**不新增 CORS 面**）；环境变量 `.env.development` / `.env.production` 均 `VITE_API_BASE=/api`，axios `baseURL` 与 SPA base **解耦**（禁止拼成 `/admin/api`）。ESLint `no-restricted-imports` 禁深链 + `dependency-cruiser` 校验分层（CI 追加 `depcruise src`）。

**理由**：开发态走网关统一入口，CORS 由网关 `cors.conf` 白名单权威下发，前端不自建代理白名单。

### D11: 新模块接入 5 步（可扩展性标尺）

1. `modules/<biz>/` 建目录（`index.ts / routes.ts / api.ts / types.ts / store.ts / views/ / components/`）；
2. `index.ts` 导出 `BizModule`（routes 每条带 `meta.title/icon/roles`）；
3. `app/router/index.ts` 追加一行 import 并合入 routes 数组；
4. 侧边菜单自动出现（由同一路由表驱动，无需改菜单配置）；
5. SSOT §3.2 矩阵加一行（若有新角色可见性决策）。

**验证**：删目录 + 删一行后 `vue-tsc --noEmit && vite build` 通过。

**理由**：5 步全部在本模块内 + 一行装配，不触碰 `shared/` 与 `app/` 其他文件——这是底座稳定的度量。

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|---|---|---|
| `frontend/` 已存在（含 `前端架构.md`），`npm create vite` 会清理目录 | 架构文档被删 | 在临时目录生成脚手架后合入 `frontend/`，或直接手写 `package.json`；**禁止对 `frontend/` 直接跑 create** |
| seed 账号不足 | 角色矩阵后两列无法实测 | ✅ 已修复：`seed_users.py` 现有 10 账号 / 5 角色，F0 可全矩阵实测 |
| qc-service 角色短码（`QC/SUPV` vs `qc_reviewer/supervisor`） | qc_reviewer 调 QC 接口必 403 | ✅ 已修复：`require_roles` 统一规范值 + 大小写不敏感，调用点全部改规范值；F2 联调时复验 |
| auth-sidecar 新增 DB 依赖 | 容器启动失败 / 网关 401 全站不可用 | DB 连接**懒加载且不阻塞启动**（否则 `/internal/authz` 被连带打挂）；DB 不可用时仅 login 返回统一信封 5xx；`/internal/authz` 保持纯无状态 |
| 401 跳转与 `redirect` 组合错误 | 登录死循环 | 守卫先判 `/login` 放行；redirect 只接受站内相对路径 |

## Migration Plan

1. 后端先行：auth-sidecar 两个端点 + `seed_users.py` 增补账号（先于前端联调）。
2. 网关：新增 auth 两 location + 豁免清单（`ENABLE_AUTH=false` 下也生效，保证零回归）。
3. 前端：脚手架 → 请求层 → auth store + 守卫 → 登录页 → 布局 + 占位路由。
4. CI：新增 `frontend` job（lint + vue-tsc + vitest + depcruise），不动现有 `common` 与 `service-test`（5 服务矩阵）。
5. 回滚：前端目录独立，未接入生产托管（F4 才做），回滚 = 不发布；后端 auth 端点新增无破坏性。

## Open Questions

无。SSOT v1.2 已冻结；qc 角色词表已修复；仅剩 F2 上传超时放宽（届时新增 `upload: 50_000` 常量）属后续期，不在本期。

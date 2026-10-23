# frontend/admin-foundation Specification

## Purpose

为智能催收 AI 平台提供 F0 期 Vue 3 管理台底座——工程初始化、唯一 axios 实例与信封解包、TIMEOUT 常量表、auth store、路由守卫与角色菜单过滤、模块契约（编译期可插拔）、主布局骨架、登录页、五模块「建设中」占位。底座业务无关：`shared/` 与 `app/` 不出现任何具体业务字段；业务能力一律落在 `modules/<biz>/`，通过「加目录 + 加装配一行」接入，不改底座。本 spec 是 `frontend/前端架构.md` v1.2（SSOT）的派生，冲突以 SSOT 为准。

## Requirements

### Requirement: 请求层必须唯一 axios 实例并注入 Bearer
全工程 MUST 只存在一个 axios 实例（`shared/request.ts`），业务代码 MUST NOT 新建实例或直接使用 `fetch`。请求拦截器 MUST 从 `request.ts` 内置 token holder 读取 token 并注入 `Authorization: Bearer <token>`（由 auth store 在 `initFromStorage / login / logout` 时写入）；`shared/` MUST NOT import `app/`（避免 `store → api → request → store` 循环）。token 为空时 MUST NOT 注入该头（禁止 `Bearer null`）。

#### Scenario: 未登录请求不注入 Bearer 头
- **WHEN** auth store 的 token 为 null 时发出请求
- **THEN** 请求头不含 `Authorization` 键（而非 `Authorization: Bearer null`）

#### Scenario: shared 层不依赖 app 层
- **WHEN** 执行 `npx depcruise src`
- **THEN** 无 `shared → app`、`shared → modules` 违规，且 `shared/request.ts` 内不出现 `@/app/` 引用

### Requirement: 响应必须统一按信封解析并解包 data
响应拦截器 MUST 对所有 HTTP 状态码统一按信封解析（网关 401/429/502/504 亦返回信封）：body 为信封且 `code` 为 `0` 或 `"0"` → 解包返回 `data`；body 非信封（超时/连接中断/HTML 404）→ 兜底提示且 trace 显示「无」；HTTP 401 或 `code === "B0002"` → 清空 auth 并跳 `/login?redirect=<当前完整路径>`（**例外：`POST /api/v1/auth/login` 自身的 401 只做表单提示，不清态、不跳转**）；HTTP 429 或 `code === "B0006"` → 读 `Retry-After` 计算退避并提示，**拦截器不自动重试**。唯一非信封白名单为 `GET /api/v1/sensitive-words/current.dict`（`text/plain`，走 `responseType:'text'` 跳过信封解析，404 映射为词典空态）。业务 API 函数 MUST 返回强类型 `data`，业务代码 MUST NOT 接触 `code/message/trace_id`。

#### Scenario: 业务代码只拿到 data
- **WHEN** 业务调用 `login()` 且后端返回 `{code: 0, message: "ok", data: {access_token: "x", token_type: "bearer", expires_in: 86400, user: {user_id: "u1", username: "admin", real_name: "系统管理员", role: "admin", status: "active"}}, trace_id: "tr_1"}`
- **THEN** 调用方只收到上述 `data`（含 `access_token` 与 `user`），无 `code` / `message` / `trace_id` 键

#### Scenario: 网关 429 信封被正确解析而非当作网络错误
- **WHEN** 网关返回 HTTP 429 + `{code: "B0006", message: "请求过于频繁", data: null, trace_id: "tr_2"}` 且响应头 `Retry-After: 5`
- **THEN** 前端走错误码白名单提示分支（非「网络异常」兜底），提示文案附 `tr_2` 后 8 位，并按 `Retry-After` 退避，不立即重试

#### Scenario: 非信封响应走兜底
- **WHEN** 请求命中 `/api/v1/stats`（网关静态根，返回 HTML 404）
- **THEN** 前端提示统一兜底文案，trace 显示「无」，不抛「code 未定义」类异常

#### Scenario: 401 清空会话并带 redirect 跳登录
- **WHEN** 除 login 自身外的请求返回 HTTP 401
- **THEN** auth store 的 `token/user/role/loaded` 全部清空、localStorage 中 `ac_token` 被删除、浏览器跳转 `/login?redirect=<当前路径>`

#### Scenario: 登录自身 401 不清态
- **WHEN** 提交错误密码，`POST /api/v1/auth/login` 返回 HTTP 401 + `B0002`
- **THEN** 登录页原地展示后端文案，auth 不被清空、不跳转（避免登录页自跳死循环）

### Requirement: 请求层必须使用 TIMEOUT 常量表且前端超时小于网关
`shared/request.ts` MUST 维护唯一 `TIMEOUT` 常量表（`normal: 12_000`、`judge: 110_000`），api 层 MUST 按端点引用常量，MUST NOT 在业务代码散写数值、MUST NOT 派生第二个 axios 实例。取值 MUST 小于对应网关 `proxy_read_timeout`（普通 15s、judge 120s）。**F0 MUST NOT 定义 `upload` 常量**：后端放宽上传超时（≥60s）前上传沿用 `normal`；放宽后新增 `upload: 50_000` 并回填 SSOT §9.3 与附录 B.2。

#### Scenario: 普通端点使用 normal 超时
- **WHEN** 调用 `/api/v1/auth/login`（同步端点）
- **THEN** 该请求使用 `TIMEOUT.normal`（12 000ms），且全工程无第二处字面量 `12000` 超时定义

#### Scenario: judge 使用独立超时常量
- **WHEN** 调用 `POST /api/v1/complaints/{id}/judge`
- **THEN** 该请求使用 `TIMEOUT.judge`（110 000ms）

### Requirement: 错误码必须走后端白名单映射，不得前端新增
`shared/utils/error-map.ts` MUST 以后端 `common/errors/codes.py` 为唯一来源维护错误码 → 用户文案映射；MUST NOT 新增后端不存在的错误码。提示文案 MUST 附 trace_id 后 8 位。`A5015/A5016/A5017`（转人审类）MUST 映射为正向提示，不得展示为失败。

#### Scenario: 未映射的错误码
- **WHEN** 后端返回一个 `error-map.ts` 中不存在的 code
- **THEN** 显示后端 `message` 原文 + trace 后 8 位，且不因缺少映射而抛异常

### Requirement: auth store 必须持久化 token 且不持久化用户信息
auth store MUST 包含 `token / user / role / loaded` 四字段（`Role` 为 `admin | supervisor | agent | qc_reviewer | ai_trainer`；`user` 为 `/me` 返回的 `{user_id, username, real_name, role, status}`）。**仅 token** MUST 持久化到 localStorage（key 固定 `ac_token`，取自 `STORAGE_KEYS.TOKEN` 常量，禁止散写字符串；auth store 是唯一 localStorage 读写点）；`user` 与 `role` MUST NOT 持久化。初始化顺序 MUST 为：`initFromStorage()` 读 token → 存在才 `fetchMe()` 重建 user/role。actions MUST 包含 `initFromStorage / login / logout / fetchMe / ensureLoaded / hasRole`；`ensureLoaded()` 在 `loaded === true` 时直接返回，否则复用同一个 in-flight Promise，保证并发路由跳转只发一次 `/me`。`logout()` MUST 清空四字段与 localStorage 并跳转 `/login`。

#### Scenario: 刷新页面保持登录态
- **WHEN** 已登录用户刷新浏览器
- **THEN** token 从 localStorage 恢复，`fetchMe()` 被调用一次并重建 user/role，页面停留在原路由而非 `/login`

#### Scenario: 角色不落盘
- **WHEN** 登录成功后检查 localStorage
- **THEN** 只存在 `ac_token` 一项，不存在 user 或 role 键

#### Scenario: 过期 token 在初始化时被清理
- **WHEN** localStorage 中的 token 已过期，用户打开应用
- **THEN** `fetchMe()` 返回 401 → auth 被清空 → 跳转 `/login`，页面不出现已登录布局

#### Scenario: ensureLoaded 并发去重
- **WHEN** 首屏同时触发多个路由跳转（`loaded === false`）
- **THEN** 只发出一次 `GET /api/v1/auth/me`，后续调用复用同一 in-flight Promise

#### Scenario: hasRole 判定
- **WHEN** 当前 role 为 `agent` 时调用 `hasRole("admin","supervisor")`
- **THEN** 返回 false；当 role 为 `admin` 调用同一参数时返回 true

### Requirement: 路由必须编译期静态装配，禁止运行时动态注册
`app/router/index.ts` MUST 是唯一装配点：静态 import 各 `modules/<biz>/index.ts` 的 `BizModule.routes` 并汇总后 `createRouter`。MUST NOT 使用 `router.addRoute` 运行时注册、MUST NOT 引入 `install(app)` 插件注册体系或运行时按需加载开关。`createRouter` MUST 使用 `history: createWebHistory(import.meta.env.BASE_URL)`，与 Vite `base: '/admin/'` 同源，保证深链刷新不 404。

#### Scenario: 装配点唯一
- **WHEN** 全仓库搜索 `addRoute(`
- **THEN** 结果为空（唯一装配为 `createRouter` 的 routes 数组）

#### Scenario: 删除模块后构建仍通过
- **WHEN** 删除任一 `modules/<biz>/` 目录并移除 `app/router/index.ts` 中对应一行 import
- **THEN** `vue-tsc --noEmit && vite build` 通过，无孤儿引用错误

#### Scenario: 深链刷新不 404
- **WHEN** 在 `/admin/` 基座下直接刷新 `/admin/complaints/123`
- **THEN** 路由命中对应页面，不被浏览器当作静态资源 404

### Requirement: 全局守卫必须按登录态与角色拦截并落到可访问路由
守卫 MUST 按序执行：① 目标为 `/login` 或 `/403` → 放行（不触发 `ensureLoaded`）；② `await auth.ensureLoaded()`（首屏 token 存在但 user 为空时调 `/me`，in-flight 去重，失败清态并抛错）；③ 仍无 token → `/login?redirect=<目标完整路径>`；④ `role` 为空 → 不计算重定向、落 `/403`；`role` 不在该路由 `meta.roles` → `/403`；⑤ 其余放行。登录成功后 MUST 优先回跳 `redirect`（仅接受站内相对路径）；无 redirect 时 MUST 跳转「当前角色按角色矩阵可访问的第一个模块路由」，无可访问路由时 MUST 落 `/403`。侧边栏菜单 MUST 由同一份路由表（`meta.title/icon/roles`）驱动。

#### Scenario: 未登录访问任意业务路由
- **WHEN** token 为空时访问任一业务模块路由
- **THEN** 跳转 `/login?redirect=<该路径>`，登录成功后回跳到该路径

#### Scenario: 越权直输 URL 被拦截
- **WHEN** `agent` 已登录并直接访问 `meta.roles` 不含 agent 的模块路由
- **THEN** 跳转 `/403`，该页面组件不渲染、不发起任何数据请求

#### Scenario: 无 complaint 权限的角色登录不进入死循环
- **WHEN** `qc_reviewer` 或 `ai_trainer` 登录成功且无 redirect
- **THEN** 落在其可访问的第一个模块路由（分别为 `/qc/tasks`、`/kb/documents`），MUST NOT 给所有角色写死 `/complaints` 造成 403 循环

#### Scenario: 菜单与守卫共用同一事实源
- **WHEN** `agent` 登录
- **THEN** 侧边栏仅显示 `meta.roles` 含 agent 的模块项，且这些项的 URL 均可达；未显示项的 URL 一律落 `/403`

### Requirement: 业务模块必须满足 BizModule 自包含契约
每个 `modules/<biz>/` MUST 满足：导出 `index.ts`（唯一出口，`BizModule = {name, routes, setup?}`）；`routes.ts` 每条路由带 `meta.title` 与 `meta.roles`；后端调用只写在 `api.ts` 且只 import `shared/request`；专属类型写在 `types.ts`（字段名与后端 snake_case 逐字一致）；页面在 `views/`、模块内组件在 `components/`。模块 MUST NOT import 其他模块任何文件，MUST NOT import `app/`。

**F0 豁免**：F0 五模块为占位路由，尚无后端调用与专属类型，故 `api.ts`、`types.ts`、`views/`、`components/` 均不创建；首次出现对应使用需求时（F1/F2）按上述契约补齐，不得以「已在 F0 缺省」为由长期空缺。

#### Scenario: 模块间零耦合
- **WHEN** 执行 `npx depcruise src --config .dependency-cruiser.cjs`
- **THEN** 无 `modules/A → modules/B`、`shared → modules`、`shared → app`、`modules → app` 违规，命令退出码 0

#### Scenario: 深链引用被 lint 阻断
- **WHEN** 代码出现 `import ... from '@/modules/complaint/views/xxx.vue'`
- **THEN** ESLint `no-restricted-imports` 报错，CI lint 步骤失败

### Requirement: 主布局必须提供侧边栏、顶栏、面包屑与 router-view
`app/layouts/AdminLayout.vue` MUST 包含：左侧可折叠侧边栏（菜单由路由表 + 当前角色过滤生成）、顶栏（当前 username、role 标识、登出按钮）、面包屑（由当前路由 `meta.title` 链生成）、中央 `<router-view>`。布局 MUST NOT 包含任何业务模块的具体 UI。

#### Scenario: 登录后进入布局
- **WHEN** 用户登录成功并落到任一可访问模块路由
- **THEN** 渲染 AdminLayout：侧边栏仅含该角色可见模块、顶栏显示 username 与 role、中央渲染目标页面

#### Scenario: 登出
- **WHEN** 点击顶栏登出
- **THEN** 会话被清空并跳转 `/login`，返回后无法通过浏览器后退进入已登录页面（守卫再次拦截）

### Requirement: 登录页必须完成凭证提交与错误展示
`app/pages/Login.vue` MUST 独立于 AdminLayout（全屏），包含用户名输入、密码输入（掩码）、登录按钮（提交中禁用防重复提交）。提交 MUST 调用 `authStore.login(username, password)`；成功按守卫规则回跳；失败 MUST 在原地展示后端 `message` 并附 trace 后 8 位（不得整页跳转）。失败文案 MUST 按 `code` 分派：`B0002` → 「用户名或密码错误」、`B0003` → 「账号已停用或锁定」（`B0003` MUST NOT 触发 clearAuth 或跳转），其余 code → `message` 原文；三者均附 trace 后 8 位（trace 缺失时显示「无」）。

#### Scenario: 正确凭证登录成功
- **WHEN** 输入 `admin / Admin@2026` 并提交
- **THEN** 按钮进入 loading → store 写入 `token/user/role` → 跳转到 redirect 或角色首个可访问路由

#### Scenario: 密码错误原地报错
- **WHEN** 输入错误密码提交（后端返回 401 + `B0002`）
- **THEN** 登录页表单区展示后端文案，按钮恢复可点击，页面不跳转、不清空用户名输入

#### Scenario: 提交中防重复
- **WHEN** 登录请求未返回时再次点击登录按钮
- **THEN** 按钮处于禁用态，不产生第二次 `/api/v1/auth/login` 请求

### Requirement: 五个业务模块必须提供「建设中」占位路由
F0 MUST 提供五个模块的占位路由（路径取自 SSOT §11.1，F1/F2 在模块内扩展子路由，不改 `app/`）：智能投诉 `/complaints`、合规质检 `/qc/tasks`、RAG 知识库 `/kb/documents`、LLM 用量 `/usage`、系统管理 `/system/users`。每个占位路由 MUST 渲染 `shared/components/base/ModulePlaceholder.vue`，显示模块名与「建设中」文案，MUST NOT 发起任何后端数据请求。每条路由 MUST 声明 `meta.title` 与 `meta.roles`，取值 MUST 与角色矩阵逐格一致：智能投诉 `["admin","supervisor","agent"]`、合规质检 `["admin","supervisor","qc_reviewer"]`、RAG 知识库 `["admin","ai_trainer"]`、LLM 用量 `["admin","supervisor"]`、系统管理 `["admin"]`。

#### Scenario: 有权限角色可达占位页
- **WHEN** `admin` 登录后点击侧边栏任一模块
- **THEN** 渲染对应占位页，显示该模块名与「建设中」，网络面板无 `/api` 数据请求

#### Scenario: 无权限角色被拦截
- **WHEN** `agent` 直接访问 `/kb/documents`
- **THEN** 跳转 `/403`，不渲染占位页

#### Scenario: 五条占位路由均存在
- **WHEN** 读取 `app/router/index.ts` 汇总后的路由表
- **THEN** `/complaints`、`/qc/tasks`、`/kb/documents`、`/usage`、`/system/users` 五条均存在且挂载在 AdminLayout 之下

### Requirement: 基础组件必须收敛在 shared/components/base
`shared/components/base/` MUST 收敛 `PageTable`、`DetailDrawer`、`StatusTag`、`TraceIdText`、`ModulePlaceholder`；其中 `ModulePlaceholder`、`TraceIdText`、`StatusTag` MUST 在 F0 可用（占位页与错误提示依赖）；`PageTable`、`DetailDrawer` MUST NOT 在 F0 提前为无使用者的场景设计，允许在首次使用期（F1/F2）补齐。基础组件 MUST NOT 依赖任何 `modules/` 或 store，数据一律 props 传入。

#### Scenario: 占位组件可用
- **WHEN** 访问任一占位路由
- **THEN** 渲染 `ModulePlaceholder` 并正确显示传入的模块名

#### Scenario: TraceIdText 复制
- **WHEN** 传入 traceId 并点击复制
- **THEN** 剪贴板内容为完整 traceId，界面展示后 8 位

### Requirement: X-Trace-Id 在 F0 仅保留扩展点，不生成不注入
F0 MUST NOT 在前端生成或注入 `X-Trace-Id` 请求头；`shared/request.ts` MUST 预留模块级 trace 上下文读写函数供 F1 使用。响应侧 MUST 从响应头 `X-Trace-Id` 或信封 `trace_id` 取值（优先响应头），用于错误提示后 8 位。业务代码 MUST NOT 手写 `X-Trace-Id`。

#### Scenario: F0 请求不携带前端生成的 trace
- **WHEN** 观察 F0 任一 `/api` 请求头
- **THEN** 不含前端生成的 `X-Trace-Id`（值由后端 middleware 生成并回传）

#### Scenario: 错误提示使用后端回传的 trace
- **WHEN** 后端返回 `trace_id: "0123456789abcdef"` 且 code 非 0
- **THEN** 提示文案附 `9abcdef` 之外的后 8 位 `89abcdef`，与响应头 `X-Trace-Id` 一致

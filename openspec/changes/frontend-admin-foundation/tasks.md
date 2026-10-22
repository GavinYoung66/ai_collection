# Tasks — frontend-admin-foundation（F0）

> 顺序即依赖。每项均给「产出路径」与「pass/fail 判据」。全部依据 `frontend/前端架构.md` v1.2（SSOT）；判据冲突时以 SSOT 为准。
> 单任务预估 0.5~2 人天。
>
> **第 1 组实现状态（2026-10-22）**：1.1–1.4、1.6 代码/配置已落地并通过静态核验；1.5 单测尚未编写；面向真容器的运行时冒烟待在 Docker 环境复验。

## 1. 后端 auth 端点（硬前置，阻塞前端真链路联调）

- [x] **1.1 增补种子账号**（`scripts/seed/seed_users.py`）
  - 判据（pass）：脚本执行后 DB 中共 **10 个账号 / 5 种角色**（admin、supervisor01/02、agent01~05、`qc_reviewer01 / Qc@2026`、`trainer01 / Train@2026`）；新增账号 `password_hash` 为 bcrypt cost=12、`status=active`；重复执行不报错（已存在则 skip）。

- [x] **1.2 auth-sidecar 增加 DB 会话**（`deploy/auth/main.py`）
  - 判据（pass）：容器内可查 `users` 表；DB 不可达时进程**仍能启动**（不得因 DB 阻塞启动，否则 `/internal/authz` 被连带打挂），此时 login 返回统一信封 5xx；现有 `/internal/authz` 行为零变化（同一 token 输入输出完全一致）；镜像内 `python -c "import passlib, bcrypt, sqlalchemy"` 成功（依赖已声明在 `deploy/auth/Dockerfile`，该服务无独立 requirements.txt）。
  - 判据（fail）：`/internal/authz` 响应字段或状态码发生变化。

- [x] **1.3 `POST /api/v1/auth/login`**
  - 判据（pass）：正确凭证 → HTTP 200 + `{code:0, data:{access_token, token_type:"bearer", expires_in, user:{user_id,username,real_name,role,status}}, trace_id}`；错误密码 / 用户不存在 → HTTP 401 + `{code:"B0002"}` + **同一文案**「用户名或密码错误」；`status=disabled|locked` → **HTTP 403 + `B0003`**「账号已停用或锁定」。
  - 判据（fail）：返回非统一信封；或用户不存在与密码错误文案不同（可枚举用户）；或停用账号返回 401。

- [x] **1.4 `GET /api/v1/auth/me`**
  - 判据（pass）：有效 token → 200 + `{code:0, data:{user_id,username,real_name,role,status}}`，且 role 为**库内当前值**（改库 role 后重调 me 返回新值，验证不读 token 旧值）；无 token / 签名错误 / 过期 / 库内用户不存在或已删除 → **401** 统一信封（`B0002`）；`status != active` → **403 + `B0003`**；内部 reason（expired / signature / audience）不泄露给客户端。
  - 判据（fail）：过期 token 返回 200；响应体暴露 `reason` 字段；改库 role 后 me 仍返回 token 内旧 role。

- [x] **1.5 pytest 单测**（`deploy/auth/tests/`）
  - 判据（pass）：覆盖 7 个用例——登录成功（`data.access_token` + `user`）/ 密码错误 401 `B0002` / 用户不存在与密码错误同文案 / disabled 账号登录 **403 `B0003`** / me 有效鉴权（user 对象）/ me 过期 401 / me 用户不存在（或已删除）401 `B0002`，`pytest` 全绿；现有测试无回归。
  - 判据（fail）：任一用例缺失或既有用例转红。

- [x] **1.6 网关新增 auth 路由 + 豁免**（`deploy/gateway/server_common.conf.template`）
  - 判据（pass）：新增 `location /api/v1/auth` 前缀反代 auth-sidecar，**不 include `auth_request.conf`**；`ENABLE_AUTH=false` 与 `true` 两态下 `POST /api/v1/auth/login`、`GET /api/v1/auth/me` 均可达（不被 auth_request 拦截）；`true` 时 `/api/v1/complaints` 仍返回 401。
  - 判据（fail）：`ENABLE_AUTH=true` 时 login 返回 401（豁免未生效）。

## 2. 工程初始化

- [x] **2.1 脚手架合入（禁止直接对 `frontend/` 跑 `npm create vite`）**
  - 做法：在临时目录生成 `vue-ts` 模板 → 复制 `package.json / tsconfig* / index.html / src/main.ts / vite-env.d.ts` 入 `frontend/`，**保留 `frontend/前端架构.md`**。
  - 判据（pass）：`frontend/前端架构.md` 仍存在且内容未被修改；`npm run dev` 可启动。
  - 判据（fail）：架构文档被删除或被脚手架模板覆盖。

- [x] **2.2 依赖安装**（`frontend/package.json`）
  - 判据（pass）：`vue-router` / `pinia` / `element-plus` / `axios` / `vitest` / `@vue/test-utils` / `eslint` / `dependency-cruiser` 已装；`npm ls` 无 peer 冲突；**ECharts 不在依赖中**。
  - 判据（fail）：出现 ECharts 或未解析的 peer 依赖。

- [x] **2.3 Vite 配置**（`frontend/vite.config.ts` + `.env.development` / `.env.production`）
  - 判据（pass）：`base: '/admin/'`（自 F0 起固定，开发入口 `http://localhost:5173/admin/`）；**`createRouter` 使用 `createWebHistory(import.meta.env.BASE_URL)`（base 三处同源）**；alias `@ → src`；`server.proxy` 将 `/api` 转发到 `http://localhost:8080`；两环境文件均 `VITE_API_BASE=/api` 且 axios `baseURL` 不拼 `/admin`；`npm run dev` 后 `curl localhost:5173/api/v1/gateway/health` 返回 `{"code":"B0000",...}` 信封。
  - 判据（fail）：proxy 返回 HTML 或连接被拒；或刷新 `/admin/<深链>` 404。

- [x] **2.4 TypeScript 严格模式**（`tsconfig.json`）
  - 判据（pass）：`strict: true`；`npx vue-tsc --noEmit` 退出码 0。
  - 判据（fail）：存在 any 逃逸或退出码非 0。

- [x] **2.5 边界工具链**（`eslint.config.js` + `.dependency-cruiser.cjs`）
  - 判据（pass）：ESLint `no-restricted-imports` 禁止 `@/modules/*/views/*`、`@/modules/*/components/*`；depcruise 规则禁 `modules/A→modules/B`、`shared→modules`、`modules→app`、以及**非 `app/main.ts` → `mocks/`**；故意写一处跨模块 import 后 `npx depcruise src` 退出码非 0。
  - 判据（fail）：违规 import 未被拦截。

- [x] **2.6 目录骨架**（`frontend/src/`）
  - 判据（pass）：`app/{main.ts,App.vue,router,layouts,pages,stores,styles}`、`shared/{request.ts,types,components/base,composables,utils}`、`modules/{complaint,qc,rag,usage,system}/`、`mocks/`（MSW，仅 DEV）均存在；`mocks/` 仅由 `app/main.ts` 按 `import.meta.env.DEV` 引入，生产构建不含 mock。
  - 判据（fail）：缺目录或模块目录放在 `shared/` 下；或 `shared/`、`modules/*/api.ts` import `mocks/`。

## 3. 请求层（`shared/`）

- [x] **3.1 信封与通用类型**（`shared/types/envelope.ts`、`shared/types/common.ts`）
  - 判据（pass）：导出 `ApiEnvelope<T> = {code: number|string; message: string; data: T; trace_id: string}`、`PageResult<T>`、`isSuccess()`、`Role` 五值联合；`vue-tsc` 0。
  - 判据（fail）：`Role` 与 `user.py` 五值不一致。

- [x] **3.2 唯一 axios 实例与拦截器**（`shared/request.ts`）
  - 判据（pass）：全仓仅 1 处 `axios.create`；`request.ts` 暴露 `setAuthToken()` 模块级 holder 且**不 import `@/app/`**；token 为空时不注入 `Authorization`；401 清 auth + `/login?redirect=`（**login 自身 401 例外：只提示不清态**）；`code===0` 返回 `data`；非 0 走 error-map；非信封响应走兜底且 trace 显示「无」；`current.dict` 白名单走 `responseType:'text'` 且 404 为空态；F0 不生成/不注入 `X-Trace-Id` 请求头（`grep -rn "X-Trace-Id" src` 仅出现在 `shared/request.ts` 响应侧读取，请求侧无 `setHeader` / 无手写注入）。
  - 判据（fail）：出现第二个实例；或 token 为空时发出 `Bearer null`。

- [x] **3.3 TIMEOUT 常量表**（`shared/request.ts`）
  - 判据（pass）：**仅** `normal: 12_000`、`judge: 110_000`（**不定义 `upload`**）；api 层引用常量；`grep -r "timeout:" src/modules` 无结果（业务层不得写数值）。
  - 判据（fail）：出现 15s/120s/30s/60s 等其他数值、`upload` 常量或第二实例。

- [x] **3.4 错误码白名单**（`shared/utils/error-map.ts`）
  - 判据（pass）：逐条覆盖 SSOT §15.1 全表（`B0001`~`B0010` 中列出的项、`A2002 / A2003 / A3004 / A5001 / A5002 / A5014 / A5015 / A5016 / A5017`）；后三者映射为正向「已转人工审核」；每个 code 有 vitest 断言；未列入的 code 走默认分支。
  - 判据（fail）：新增后端不存在的错误码。

- [x] **3.5 请求层 Vitest**（`src/shared/__tests__/request.spec.ts`）
  - 判据（pass）：7 个用例全绿——信封解包返回 data / 401 跳登录 / **login 自身 401 不清态** / 非 0 code 提示带 trace 后 8 位 / 429 走白名单且不自动重试 / 非信封走兜底 / token 空不注入；`vitest run` 退出码 0。
  - 判据（fail）：任一用例红或断言仅覆盖 happy path。

## 4. auth store 与路由守卫

- [x] **4.1 auth store**（`app/stores/auth.ts`）
  - 判据（pass）：字段 `token/user/role/loaded`；**仅 token** 持久化（key `ac_token`）；actions `initFromStorage/login/logout/fetchMe/ensureLoaded/hasRole` 齐备；`ensureLoaded()` in-flight 去重；`logout()` 清空四字段并跳 `/login`。
  - 判据（fail）：user 或 role 写入 localStorage；或缺 `ensureLoaded`。

- [x] **4.2 storage key 常量**（`shared/utils/constants.ts`）
  - 判据（pass）：文件只导出 `STORAGE_KEYS.TOKEN = 'ac_token'` 常量（**不建统一 storage 管理抽象**，SSOT §18 明确不做）；`grep -r "localStorage" src` 仅出现在 `app/stores/auth.ts`。
  - 判据（fail）：业务代码散写 key 字符串（如 `ai_admin_token`）。

- [x] **4.3 路由装配与守卫**（`app/router/index.ts`、`app/router/guards.ts`）
  - 判据（pass）：静态 import 汇总各 `BizModule.routes`；`grep "addRoute("` 无结果；`createRouter` 使用 `createWebHistory(import.meta.env.BASE_URL)`；守卫顺序为 `/login`、`/403` 放行 → `await ensureLoaded()`（去重）→ 无 token 跳登录（带 redirect）→ `role` 为空或角色不符跳 `/403` → 放行。
  - 判据（fail）：使用 `router.addRoute` 或运行时插件注册。

- [x] **4.4 角色首页落点函数**（`app/router/index.ts` 或 `guards.ts`）
  - 判据（pass）：`firstAccessibleRoute(role)` 返回该角色按 SSOT §11.1 可访问的第一条路由——admin→`/complaints`、supervisor→`/complaints`、agent→`/complaints`、qc_reviewer→`/qc/tasks`、ai_trainer→`/kb/documents`；无可访问路由时返回 `/403`；登录无 redirect 时使用该函数（**禁止给所有角色写死 `/complaints`**）。
  - 判据（fail）：qc_reviewer 或 ai_trainer 登录后进入 403 循环。

- [x] **4.5 守卫 Vitest**（`src/app/__tests__/guards.spec.ts`）
  - 判据（pass）：6 用例全绿——未登录跳 `/login?redirect=` / 越权跳 `/403` / redirect 仅接受站内相对路径 / 无 redirect 按角色落点 / `ensureLoaded` 并发只发一次 `/me` / `role` 为空不递归重定向。
  - 判据（fail）：任一用例红。

## 5. 登录页与真链路联调

- [x] **5.1 登录页**（`app/pages/Login.vue`）
  - 判据（pass）：独立全屏（不在 AdminLayout 内）；用户名 + 密码（掩码）+ 登录按钮；提交中按钮禁用（100ms 内连点只发 1 次请求）；失败原地展示后端 message 且不清空用户名。
  - 判据（fail）：失败时整页跳转；或连点发出 2 次请求。

- [x] **5.2 真实后端联调**（账号以 `seed_users.py` 为准）
  - 判据（pass）：`admin / Admin@2026`、`supervisor01 / Super@2026`、`agent01 / Agent@2026`、`qc_reviewer01 / Qc@2026`、`trainer01 / Train@2026` 五个账号均可登录并进入对应落点路由；DevTools 中 `/api/v1/auth/login` 返回 `code:0` 且响应头有 `X-Trace-Id`。
  - 判据（fail）：任一账号登录失败，或落点与 4.4 表不符。

- [x] **5.3 错误路径**（手动）
  - 判据（pass）：错误密码 → 原地 401 + `B0002`「用户名或密码错误」；禁用/锁定账号 → **403 + `B0003`**「账号已停用或锁定」（前端按 code 提示，不当作登录态失效）；两者提示均带 trace 后 8 位。
  - 判据（fail）：出现白屏或未捕获异常。

- [x] **5.4 会话保持与过期**（手动）
  - 判据（pass）：登录后刷新页面仍停留原路由（不跳 `/login`）；手动把 localStorage token 改为过期值后刷新 → 跳 `/login?redirect=`。
  - 判据（fail）：刷新即掉登录；或过期 token 仍可进入业务路由。

## 6. 主布局与占位路由

- [x] **6.1 主布局**（`app/layouts/AdminLayout.vue`）
  - 判据（pass）：侧边栏可折叠 + 菜单按角色过滤、顶栏显示 username/role/登出、面包屑按 `meta.title` 链生成、中央 `<router-view>`；布局内无任何业务模块具体 UI。
  - 判据（fail）：菜单出现当前角色无权项；或布局中 import 了 `modules/*`。

- [x] **6.2 五模块占位路由**（`modules/<biz>/{index.ts,routes.ts}`）
  - 判据（pass）：`/complaints` `/qc/tasks` `/kb/documents` `/usage` `/system/users` 五条路由存在并挂载于 AdminLayout 下（路径取自 SSOT §11.1）；每条 `meta.roles` 与 SSOT §3.2 矩阵逐格一致；页面渲染 `ModulePlaceholder` 显示模块名 + 「建设中」；网络面板无 `/api` 数据请求。
  - 判据（fail）：缺任一模块；或 meta.roles 与矩阵有一格不符；或占位页发出数据请求。

- [x] **6.3 基础组件**（`shared/components/base/`）
  - 判据（pass）：`ModulePlaceholder`、`TraceIdText`（展示后 8 位 + 一键复制完整值）、`StatusTag` 可用；三者不依赖任何 store 或 `modules/`；`PageTable` / `DetailDrawer` 允许留空（F1/F2 首次使用时补齐）。
  - 判据（fail）：组件内 import `modules/*` 或 store。

- [x] **6.4 角色矩阵实测（5 角色 × 5 模块）**
  - 判据（pass）：逐格与 SSOT §3.2 一致——菜单可见性与直接输 URL 结果均为「符合/403 拦截」；**qc_reviewer / ai_trainer 均可正常登录进入各自落点，不出现 403 循环**（qc-service 角色短码已修复；F0 为占位页，接口级联调留 F2）。
  - 判据（fail）：出现「菜单可见但 URL 被拒」或反向不一致。

- [x] **6.5 可插拔验证**
  - 判据（pass）：`npx depcruise src` 退出码 0；删除任一 `modules/<biz>/` 并移除装配一行后 `npx vue-tsc --noEmit && npx vite build` 通过；恢复后再次通过。
  - 判据（fail）：删除模块后出现孤儿引用或构建失败。

## 7. CI 门禁

- [x] **7.1 新增 `frontend` job**（`.github/workflows/ci.yml`）
  - 判据（pass）：job 内依次执行 `eslint` + `npx vue-tsc --noEmit` + `npx vitest run` + `npx depcruise src`；四步全绿。
  - 判据（fail）：任一命令被 `|| true` 吞掉（必须真实阻断）。

- [x] **7.2 不动现有 2 个 job（`common` + `service-test` 5 服务矩阵）**
  - 判据（pass）：`common` + `service-test`（5 个 matrix）保持原样，名称、步骤、顺序未改；新增 required check 仅追加 `frontend`（**GitHub 分支保护为仓库设置，需人工在 Settings → Rules 中追加，代码无法自动完成**）。
  - 判据（fail）：现有 job 被重命名、合并或删除。

- [x] **7.3 门禁生效验证**
  - 判据（pass）：构造一次前端 lint 错误（临时文件）→ `npm run lint` 退出码非 0；删除临时文件 → 再跑退出码 0。
  - 判据（fail）：错误提交仍可通过 checks。
  - 说明（批次 6 边界）：本仓纪律禁止 commit/push，故「CI 侧红色 run」改为人工动作——推送后观察 `frontend` job 是否阻断；本地红→绿已取证，CI 侧结论标注为**待人工推送验证**。

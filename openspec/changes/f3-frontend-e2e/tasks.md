# Tasks

## 1. 依赖与运行配置

- [ ] 1.1 新增 `devDependency`：`@playwright/test` + 安装 chromium 内核；验证：`npx playwright --version` 可执行，`package.json` 仅多出测试期依赖（运行时 dependencies 零新增）
- [ ] 1.2 编写 `frontend/playwright.config.ts`：`baseURL = http://localhost:5173/admin/`（对齐 vite `base: '/admin/'`）、`webServer` 自动拉起 `npm run dev` 并等待端口就绪、单 chromium、`retries: 1`、`trace: 'on-first-retry'`、`screenshot: 'only-on-failure'`、产物目录 `test-results/` 加入 `.gitignore`；验证：`npx playwright test --list` 正常输出
- [ ] 1.3 `package.json` 增加 `test:e2e` 脚本；验证：脚本可执行且不改变既有 `test`（vitest）语义

## 2. 网络隔离基线

- [ ] 2.1 编写统一 mock 分派（`e2e/support/mock-api.ts`）：`page.route('**/api/**')` 按端点返回固定信封 `{code,message,data,trace_id}`，覆盖登录 `/api/v1/auth/login`、`/auth/me`、qc 15 端点最小集（demo/pending、records、records/{sessionId}、reviews/pending、alerts、rules/active）；验证：任一用例中断言页面无真实网络请求（`page.on('request')` 拦截计数为 0 命中非 mock 地址）
- [ ] 2.2 提供三角色登录夹具（admin / supervisor / qc_reviewer），登录态经 mock 而非真实后端；验证：三角色均可进入 `/admin/` 首页且侧边栏按角色渲染

## 3. 导航壳层用例（F2 缺陷回归）

- [ ] 3.1 用例：跨一级菜单切换后二级菜单保持展开——`点击「LLM 用量」→ 点击「合规质检」→ 等待 1.5s → 断言二级菜单仍展开`（复刻 F2 缺陷触发路径，锁死延迟折叠回归）；验证：本地跑通，且在 F2 缺陷版本上应失败（若可复现旧实现则做红绿对照）
- [ ] 3.2 用例：同组重复点击折叠、不同分组互斥展开；验证：断言展开态集合与预期一致
- [ ] 3.3 用例：点击父级标题后 URL 落到该分组第一个子路由，且父级保持展开；验证：`expect(page).toHaveURL(/\/qc\/tasks/)` + 展开态断言

## 4. 角色路由用例

- [ ] 4.1 用例：三角色 × 6 条 qc 路由的侧边栏可见性断言（admin 无复核、supervisor 无规则、qc_reviewer 仅三条）；验证：逐角色断言菜单项集合
- [ ] 4.2 用例：越权直输 URL 落 `/403`（qc_reviewer 访问 `/qc/rules`、`/qc/alerts`；admin 访问 `/qc/reviews`），且不发起对应数据请求；验证：URL 断言 + 请求计数断言

## 5. qc 主链路 UI 用例

- [ ] 5.1 用例：任务台行内「开始质检」在途时按钮禁用，重复点击只发一次请求；验证：mock 计数为 1
- [ ] 5.2 用例：详情页人审按钮组按角色呈现——admin 置灰 + 说明、qc_reviewer/supervisor 可用；验证：可见性与 `disabled` 属性断言
- [ ] 5.3 用例：零扣分记录不渲染提交按钮；验证：按钮不存在

## 6. CI 接入与门禁自测

- [ ] 6.1 `.github/workflows/ci.yml` 新增 `frontend-e2e` job（与静态四闸并行、`npm ci` + 安装 chromium + `npm run test:e2e`、失败上传 `test-results/`），禁止 `continue-on-error`；验证：YAML 语法通过 + 本地等价命令全绿
- [ ] 6.2 门禁自测：故意改坏 3.1 的断言，确认本地与 CI 均变红，随后还原；验证：留存红/绿两次输出证据（防止 E2E 成为永不失败的摆设）

## 7. 验收与文档

- [ ] 7.1 四道静态闸零回归：`npm run lint`、`npm run typecheck`、`npm test`、`npm run depcruise` 全部 exit 0；验证：退出码记录
- [ ] 7.2 全量 E2E 本地与 CI 双绿，且 F2 遗留的「浏览器 UI 人工抽查」由 3.1–3.3 覆盖；验证：逐用例 pass 表
- [ ] 7.3 回填 F2 tasks.md 遗留项引用本 change，说明人工抽查已自动化替代；验证：`grep -n "f3-frontend-e2e" openspec/changes/f2-qc-console/tasks.md` 命中

## Workflow follow-up

- 本 change 不修改 SSOT《frontend/前端架构.md》（见 design D6）。
- 若后续引入视觉回归或真实链路 E2E，须另立 change，不在本批扩范围。

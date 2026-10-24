# Proposal

## Why

F2 合规质检交付期间，导航二级菜单「从其他一级菜单切回后展开 1 秒即自动折叠」的时序缺陷，在四道静态门禁全绿、228 条单测全绿的情况下完整漏网，最终只能靠人工浏览器反复复现与探针取证才定位（根因：父级标题的 `@click.capture` 导航与同次点击冒泡到 el-sub-menu 的 toggle 并发竞争，懒加载路由异步切换后展开状态被覆盖）。

同批真链路冒烟阶段，浏览器自动化工具环境系统性超时，真实 UI 验收被迫降级为 route-matrix 单测 + 接口级三角色矩阵，tasks 8.3 只能注明「建议人工抽查」。这暴露一个结构性缺口：**jsdom 没有真实布局、懒加载 chunk 加载、动画帧与事件阶段，天然无法覆盖交互时序类缺陷**；而静态门禁（lint / 类型 / 单测 / 分层依赖）只能证明代码静态正确。

本 change 建立前端交互层的确定性回归基础设施，使「菜单展开、路由守卫、角色驱动的按钮态」这类壳层行为可在 CI 中重复验证，不再依赖人工抽查。

## What Changes

- 引入 **Playwright** 作为唯一新增依赖（`devDependency`，测试期专用，不进入构建产物），配置 `baseURL = http://localhost:5173/admin/`（vite `base: '/admin/'`），`webServer` 自动拉起 vite dev，CI 仅用 chromium 单一浏览器。
- **网络隔离**：全部 `/api/**` 由 `page.route` mock 返回固定信封，E2E 只断言**交互行为与路由结果**，不断言数据正确性——数据契约已由 F2 的 15 端点契约单测与接口级三角色矩阵覆盖，避免重复建设与后端容器依赖（历史上 RocketMQ / qc-service 镜像漂移曾多次造成无关失败）。
- 首批用例三组：
  1. **导航壳层**：跨一级菜单切换后二级菜单保持展开（F2 bug 的回归用例）、同组重复点击折叠、分组互斥；
  2. **角色路由**：admin / supervisor / qc_reviewer × 6 条 qc 路由的菜单可见性，以及越权直输 URL 落 `/403`（把现有 route-matrix 单测提升为浏览器级复验）；
  3. **qc 主链路 UI**：任务台行内开检防连点、详情页人审按钮按角色置灰/可用、零扣分不渲染提交按钮。
- CI 新增 `frontend-e2e` job（与四道静态闸并行，失败即失败，`retries: 1`、`trace: on-first-retry`）。
- **门禁自测**作为验收项：故意改坏一条断言，必须观察到 job 变红，再还原——防止 E2E 变成「永不失败的摆设」。
- 不修改 SSOT《frontend/前端架构.md》：E2E 属研发基础设施，不涉及对外契约（路由/方法/信封/错误码/角色词表/网关超时）变更，也不构成架构决策变更，不满足该文档允许修订的两类触发。
- 不修改任何业务代码与 qc 模块实现；不改后端；不改网关。

## Capabilities

### New Capabilities

- `frontend/e2e-harness`: 前端交互层确定性回归基础设施——基于 Playwright 的浏览器级壳层用例（导航展开语义、角色路由守卫、qc 关键按钮态），零后端依赖、CI 可重复执行，用于拦截静态门禁与 jsdom 单测不可见的时序/表现类缺陷。

### Modified Capabilities

（无。现有 `frontend/admin-foundation`、`frontend/complaint-console`、`frontend/qc-console` 的 REQUIREMENT 均不变，本 change 只新增验证手段。）

## Impact

- **依赖**：新增 1 个 `devDependency`（Playwright + chromium 内核），无运行时依赖，产物体积不变。
- **代码**：新增 `frontend/e2e/**`（用例与 mock 基线）与 `frontend/playwright.config.ts`；`package.json` 增加 `test:e2e` 脚本；业务源码零改动。
- **CI**：新增 `frontend-e2e` job，流水线时长增加约 2–4 分钟。
- **验证**：E2E 全绿 + 门禁自测（改坏断言可观察变红）+ 既有四道静态闸零回归。

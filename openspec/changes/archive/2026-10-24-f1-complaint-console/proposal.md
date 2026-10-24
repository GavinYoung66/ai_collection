# Proposal

## Why

F0 管理台底座已归档（master CI 全绿），`/complaints` 仍为「建设中」占位页；而旧演示页 `services/complaint-service/web/`（纯 HTML + 原生 JS）已承载智能投诉全链路演示能力，但与后端 16 个已上线端点脱节、无工程化保障。本批次（F1）将智能投诉功能完整重构平移进 Vue 3 管理台，使投诉坐席/主管/管理员获得与 SSOT《frontend/前端架构.md》v1.2 §5.0 B 表逐条对齐的正式生产界面。

## What Changes

- 新建 `frontend/src/modules/complaint/` 完整业务模块（`index.ts` / `routes.ts` / `api.ts` / `types.ts` / `views/` / `components/`），替换 F0 占位路由，满足 BizModule 自包含契约。
- 接入 §5.0 B 表全部 16 个端点（一律 `/api/v1/complaints/*` 全前缀）：列表、手工建单、演示灌数、事件、详情、judge（110s 长请求）、status 轮询、verdict、agent-logs、trace、disposition/verdict、disposition/callback、review、close、stats、mode。
- 路由按 SSOT §11.1：`/complaints`（ComplaintList，keepAlive）、`/complaints/review`（ComplaintReview，声明在 `:id` 之前，复核工作台 = 列表页待复核筛选视图，状态集合以后端 `state.py` 为准）、`/complaints/:id`（ComplaintDetail）；`meta.roles` 均为 `admin/supervisor/agent`，`meta.title/icon` 齐全，视图组件一律 `() => import()` 懒加载（§15）。
- 列表页：分页（page 从 1 起、page_size 默认 20、上限 100）、状态筛选、行内研判/详情；「演示灌数/事件」入口仅 `import.meta.env.DEV` 可见。
- 详情页：状态步骤条、AI 初选与人审定责对照、结论卡（`verdict/confidence/conflicts`）、证据包（`ordered_evidence`/`evidence_refs`，RAG 源 ID 回溯）、定责确认 / 回电登记 / 人工复核 / 关单 / 重新研判操作组；Tab 按需加载；轮询严格满足 §14.1 契约（3s 起退避、终态停止补拉、失败熔断、unmount 清理），轮询生命周期只由 `task_status` 驱动。
- X-Trace-Id 在 F1 落地（§9.4）：拦截器注入 + 详情页 store/composable 写入 trace 上下文，串联「查看→操作」复合链路。
- 错误处理按 §15.1 白名单落实模块侧动作（A5001 空态、A5002 自动刷新、A5014 强警示降级、B0005 幂等提示、B0010 不重试、A5015-17 正向提示跳复核队列）。
- 统计展示（`GET /complaints/stats`，不引 ECharts）；运行模式切换（`PUT /complaints/mode`，双签确认；`GET /mode` 为后端待补端点，缺失时不展示当前模式）。
- `PageTable` 首次下沉 `shared/components/base/`（F0 预留坑位）；`DetailDrawer` 若被详情 Tab 使用则一并下沉。
- Vitest 契约与交互单测：16 端点 method+path、分页默认 20、judge 用 `TIMEOUT.judge`、轮询启停、防连点、错误码走 error-map 白名单。
- 废弃旧演示页「pending 自动研判」行为（改为行内手动触发）；不引入 SSE/WebSocket/ECharts；不改后端、不改网关、不改其他四模块。

## Capabilities

### New Capabilities

- `frontend/complaint-console`: 智能投诉管理台前端模块——投诉单列表/建单/详情（进度轮询、证据、agent 日志、trace、结论卡）、定责/回电/复核/关单操作组、复核工作台、统计展示与运行模式切换，契约以 SSOT §5.0 B 表 16 端点为准。

### Modified Capabilities

（无。F0 `frontend/admin-foundation` 已显式预留本批次演进点：占位路由按模块契约替换、PageTable/DetailDrawer 允许首次使用期下沉，无 REQUIREMENT 级变更。）

## Impact

- **代码**：新增 `frontend/src/modules/complaint/**`；`shared/components/base/` 新增 PageTable（及可能的 DetailDrawer）；`shared/request.ts` 请求拦截器补 X-Trace-Id 注入（唯一一处 shared 既有文件改动，其余逻辑不动）；`app/router/index.ts` 装配行零改动（模块名不变，占位 routes 在模块内被替换为实现路由）。
- **API**：消费后端既有 16 端点，无后端/网关变更；`GET /api/v1/complaints/mode` 列为后端待补（前端降级处理）。
- **依赖**：不新增任何运行时依赖（不引 ECharts/SSE/WebSocket）。
- **验证**：四道 CI 门禁（lint / vue-tsc / vitest / depcruise）须保持全绿；退出标准含真实浏览器全链路冒烟。

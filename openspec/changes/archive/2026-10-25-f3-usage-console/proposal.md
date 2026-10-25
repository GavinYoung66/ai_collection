# Proposal

## Why

F0 底座、F1 智能投诉、F2 质检与 RAG 均已归档；`/usage` 仍为 F0 占位页（`modules/usage/routes.ts` → ModulePlaceholder）。SSOT《frontend/前端架构.md》§5.5 规划了 LLM 用量看板（指标卡/分布/趋势）、调用明细与 trace_id 检索，§11.1 已登记 `/usage` 路由（admin/supervisor、keepAlive），但后端两个端点（`GET /api/v1/llm-usage/summary`、`/logs`）至今「待实现」（SSOT:251-252），网关亦无对应 location。本批以**前端先行 + DRAFT 契约 + mock 驱动**的方式把页面骨架与行为契约先落地，待后端端点与埋点补齐后可零返工接入真链路——这是唯一能在不阻塞等后端的前提下推进 F3 的路径。

## What Changes

- 替换 `frontend/src/modules/usage/routes.ts` 的占位组件为真实视图，新建模块完整结构（`index.ts` / `types.ts` / `api.ts` / `views/UsageDashboard.vue` / `components/` / `__tests__/`），满足 BizModule 自包含契约（§7.1）；`app/router/index.ts` 装配行零改动（usageModule 已挂载）。
- 路由保持 §11.1:717 唯一一条：`/usage`，name=`UsageDashboard`，`meta.roles=['admin','supervisor']`，keepAlive=true，懒加载，`defineOptions` name 与路由 name 逐字一致；**不新增子路由**，看板与明细同页 Tab 承载；同步更新 `app/__tests__/route-matrix.spec.ts`。
- 接入 2 个 DRAFT 端点（`/api/v1/llm-usage/summary`、`/logs`，全前缀 + `TIMEOUT.normal`=12s）：字段来源为 `common/db/models/llm_log.py:14-27` 的既有列，**不新增列名、不自造枚举**；`status` 字面值以 `llm_log.py:25` 注释为准（success/failed/timeout/circuit_open）。契约以「DRAFT（待后端确认）」身份写入 spec，不表述为已实现。
- 用量看板（§5.5:436-440）：4 张指标卡（调用量 / token 总量 / 平均时延 / 失败率）+ 时间范围（近 1 天 / 7 天 / 30 天 / 自定义）+ 模型分布图 + 调用量×失败率双轴趋势 + 服务分布图（`by_service` 缺失时整块隐藏）。
- 调用明细（§5.5:441-444）：分页表（model/status/latency/token，page_size 默认 20、1~100 前端双向钳制）、status 筛选（含「失败」快捷项）、`trace_id` 精确检索（结果用共享 TraceIdText 可复制）、`error_msg` 行展开。
- 韧性状态（§5.5:445-446）：取 `summary.resilience`（限流/熔断/fallback 计数），缺失时整块隐藏，**不在前端自算**。
- 诚实呈现：页面固定横幅声明「用量数据当前仅 qc-service 上报；complaint/rag 埋点与 token/时延字段补齐前，分布与均值可能不完整」，避免看板被误读为全量真实数据。
- 新增唯一运行时依赖 **echarts**（SSOT:466 明确「仅 LLM 用量模块，F3 再装」），MUST 按需引入（`echarts/core` + PieChart/LineChart + Grid/Tooltip/Legend + CanvasRenderer），禁止整包 import。
- 错误处理严格按 §15.1 白名单，零新增映射；看板空态以 `calls===0` 判定、明细空态以 `total===0` 判定。
- 开发态 mock 沿用 `src/mocks/browser.ts` 的 axios 自定义 adapter（msw 未安装，禁新增依赖），补 summary / logs 两段 handler，并覆盖「缺 by_service/resilience 的第二种形态」。
- SSOT 闭环（§13）：回填 §5.5 状态标注「前端已实现（DRAFT 契约 + mock 驱动），后端端点待实现」、§4 路线表 F3 行状态、附录 B.2（版本号递增 + 变更登记）；**不得改写 SSOT 已登记的后端事实**（如把「待实现」改成「已实现」）。
- **砍项**（写入 design Non-Goals 并说明理由）：不做子路由/明细独立页（§11.1 未登记，改需先回填）；不做前端指标自算（Prometheus `/metrics` 内部可达，前端无通道）；不做权限按钮（只读页，admin/supervisor 全量可见，无后端写接口）；不新增 `svc` 列 migration（登记为后端待办）。

## Capabilities

### New Capabilities

- `frontend/usage-console`: LLM 用量看板前端能力（F3）——指标卡 / 时间范围 / 模型与服务分布 / 双轴趋势 / 调用明细分页与失败筛选 / trace_id 检索 / 韧性计数展示；接口契约为 DRAFT，后端端点与埋点待实现。

### Modified Capabilities

（无：不改变已有 capability 的行为要求。）

## Impact

- **前端**：替换 `frontend/src/modules/usage/routes.ts`；新增模块 `index.ts` / `types.ts` / `api.ts` / `views/UsageDashboard.vue` / `components/UsageStatCards.vue`、`UsageTrendChart.vue`、`UsageModelChart.vue`、`UsageServiceChart.vue` / `__tests__/`；`app/__tests__/route-matrix.spec.ts` 同步 1 条断言；`src/mocks/browser.ts` 新增 2 段 handler；`package.json` 新增 `echarts`（按需引入）。
- **文档**：SSOT §5.5 状态标注、§4 路线表 F3 行、附录 B.2 回填。
- **不动**：后端任何服务、网关模板、`shared/` 与 `app/` 既有逻辑、其他五个模块；不新增子路由；不建模块 store。
- **已知不能验收**：真链路（端点不存在、网关无 location、埋点缺口），本批验收以 mock 驱动 Scenario 全绿 + DRAFT 契约对齐状态为准，原「与 llm_call_logs 表 count 一致」退出标准本批不适用。

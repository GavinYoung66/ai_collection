# Design

## Context

`frontend/src/modules/usage/` 当前仅有占位路由（`routes.ts` → `ModulePlaceholder`）。底座机制均已就绪：`shared/request` 单实例（信封解包 / `isSuccess` / `TIMEOUT` 常量表，§9）、路由守卫与 keepAlive 机制（§11/§11.1 注）、shared base 组件 `PageTable` / `StatusTag` / `TraceIdText`、error-map 白名单（§15.1）、开发态 mock（`src/mocks/browser.ts` 的 axios 自定义 adapter，双重门控）。

后端事实（已核实）：`/api/v1/llm-usage/summary`、`/logs` 在所有服务中均不存在（SSOT:251-252 标「待实现」）；网关无 `/api/v1/llm-usage` location；`llm_call_logs` 表存在（`common/db/models/llm_log.py:14-27`）但**无 svc 列**，写入方仅 qc-service（`llm_reviewer.py:254`）且未写 `token_usage` / `latency_ms` / `temperature`；complaint / rag 走 `invoke_llm` 但不落该表；Prometheus 指标族已存在（`llm_metrics.py:37-68`）但 `/metrics` 仅内部可达。另：`llm_log.py:25` 的 status 列注释（success/failed/timeout/circuit_open）与 qc 实际写入值（`ok` / `qc_review_exemption` / `skipped`）不一致，见 Open Questions。

约束：本批只做前端；不改后端、网关、`shared/`、`app/` 既有逻辑与其他五模块；除 echarts 外无新增运行时依赖；不建模块 store。

## Goals / Non-Goals

**Goals:**
- 替换 `/usage` 占位为可用看板，逐条对齐 §5.5:436-446 功能树
- 固化 2 个 DRAFT 端点的前端契约（字段对齐 `llm_log.py` 列，标注待后端确认）
- 数据缺失时整块隐藏（`by_service` / `resilience`），页面不出现空图表或误导性零值
- 可插拔反向验证通过；四道门禁全绿

**Non-Goals:**
- 不做子路由 / 明细独立页（§11.1 仅登记 `/usage`；改需先回填 §11.1）
- 不做前端指标自算（Prometheus 无前端通道）；不以 Grafana 数据替代
- 不做权限按钮（本页只读，无后端写接口）
- 不新增 `svc` 列 migration、不改 qc 写入字段、不加网关 location（均登记为后端待办）
- 不做真链路验收（端点/网关/埋点三者皆缺）

## DRAFT 契约草案（tasks 1.1）

字段名一律锚定 `common/db/models/llm_log.py:14-27`，不新增列名；两个端点后端均未实现，以下为前端草案，后端落地后以 `/openapi.json` 为准。

**GET /api/v1/llm-usage/summary**

请求参数（query）：

| 参数 | 类型 | 说明 |
|---|---|---|
| start | string（ISO8601） | 起始时间（含） |
| end | string（ISO8601） | 结束时间（不含）；与 trend 分桶边界同属 Open Questions |

响应 `data`：

| 字段 | 类型 | 来源 / 说明 |
|---|---|---|
| totals.calls | number | `count(*)`，空态判据（spec R3） |
| totals.tokens | number | `sum(token_usage.total_tokens)`（`:21`） |
| totals.avg_latency_ms | number \| null | `avg(latency_ms)`（`:22`），null 是否排除待确认 |
| totals.failure_rate | number（0~1） | 失败占比，分母待确认（Open Questions） |
| by_model[] | `{model, calls, tokens}` | `group by model`（`:18`） |
| by_service[]? | `{service, calls}` | 无 svc 列，结构性缺失；缺失即整块隐藏（D4） |
| trend[] | `{bucket_start, calls, failure_rate}` | `group by create_time`（`:27`）分桶 |
| resilience? | `{rate_limited, breaker_open, fallback}` | 缺失即整块隐藏（D4） |

**GET /api/v1/llm-usage/logs**

请求参数（query）：`page`、`page_size`、`status?`、`provider?`、`trace_id?`、`start`、`end`

响应 `data`：`items[]` 逐列映射（`id:14`、`trace_id:15`、`session_id:16`、`provider:17`、`model:18`、`token_usage:21`、`latency_ms:22`、`confidence:23`、`status:25`、`error_msg:26`、`create_time:27`）+ `total` / `page` / `page_size`。

status 实际写入值：`ok`（`llm_reviewer.py:466`）、`qc_review_exemption`（`:390/:407/:425/:436/:449/:459`）、`skipped`（`:397`）；`llm_log.py:25` 注释四值仅计划值。前端只做原样透出，筛选项待后端确认。

## Decisions

**D1｜前端先行 + DRAFT 契约 + mock 驱动。** 后端端点、网关 location、埋点三项均缺，等后端则 F3 完全阻塞。前端行为契约（时间范围、分页、筛选、缺失隐藏、空态）与后端实现解耦度足够高，可先固化并以 mock 验证；后端点落地后只需替换 mock 开关即可接真链路。替代方案（先做后端变更）会把本阶段拖成跨团队串行，且前端契约反而更晚定型。

**D2｜看板与明细同页 Tab，不新增子路由。** §11.1:717 仅登记 `/usage` 一条且 keepAlive=true；新增 `/usage/logs` 需先回填 SSOT 并改 route-matrix 语义，收益仅是可深链到明细页。当前使用场景是「看整体 → 钻到明细」的同页动作，Tab 足够；替代方案（子路由）成本大于收益，留待有外链需求时再议。

**D3｜echarts 按需引入，不用整包。** 仅需 2 个 series（line/pie）+ 4 个组件（Grid/Tooltip/Legend/Title 之一）+ CanvasRenderer；整包会引入地图、关系图等无关模块。SSOT:466 已把 echarts 授权给本模块。替代方案（引 vue-echarts 封装）会多一层依赖，收益仅是模板语法糖，不值。

**D4｜服务分布与韧性块条件渲染。** `llm_call_logs` 无 svc 列、且指标族计数未透出到 HTTP，两处数据在当前后端形态下结构性缺失。选择「字段缺失即整块隐藏」而非「前端推导」：前端推导只能靠 provider/model 猜服务，属于编造事实；隐藏则在后端补齐后自动出现，零返工。

**D5｜分页与筛选状态用组件本地 ref。** 照搬 qc `QcRecordList` 先例；§8 规划的 `useTable` / `usePagination` 无实体，禁止引用。跨 Tab 共享时间范围用同一视图内的 ref 传递，不建模块 store（§10）。

**D6｜图表生命周期与 keepAlive 显式对齐。** 缓存返回时 DOM 尺寸可能为旧值，必须在 `onActivated` 重绘并重新测量；`resize` 监听在 `onDeactivated` / `onUnmounted` 移除，避免导航多次后监听泄漏。替代方案（关闭 keepAlive）违反 §11.1 因此不选。

**D7｜诚实横幅常驻，不放宽为条件提示。** 数据完整性缺口是系统级事实，不是某次查询的偶发状态；横幅若随「有无数据」变化会被误读为数据已完整。因此无条件常驻，并由 Scenario 固定断言。

**D8｜mock 沿用 axios 自定义 adapter（msw 未安装）。** 与 F2 同构：`src/mocks/browser.ts` 内新增 summary / logs 两段 handler，`DEV` + `VITE_USE_MOCK` 双重门控，生产构建不打包；同时提供「缺 by_service/resilience」的第二形态用于验证隐藏逻辑。**偏差登记**：设计初期曾计划 MSW，核实 `package.json` 无 msw（仅 optional peer），本批禁新增依赖，故沿用等价 adapter。

## Risks / Trade-offs

- **数据完整性缺口（高）**：无 svc 列、仅 qc 写入、token 与 latency 未落库 → 上真链路后指标卡（token 总量 / 平均时延）与模型分布可能大面积为空或为 null。处置：条件渲染 + 常驻横幅 + design Open Questions 登记三项后端待办；不在前端造数。
- **DRAFT 契约漂移（中）**：后端实现时若字段改名或聚合口径不同（如 failure_rate 分母），前端需同步返工。处置：tasks 1.1 强制与后端对齐并就地修订 spec/design；契约字段全部锚定 `llm_log.py` 列名，避免自造命名。
- **原退出标准不可达（中）**：SSOT:116 的「与 llm_call_logs 表 count 一致」在本批无法验证（表内仅 qc 且无前端可访问端点）。处置：改用「mock 驱动 Scenario 全绿 + 契约对齐状态」，并在 Open Questions 标注待后端变更落地后复核。
- **echarts 单测盲区（低）**：jsdom 无 canvas，图表真实渲染无法断言。处置：单测 mock echarts，仅断言 `setOption` 入参（series 数据、双轴配置）与生命周期调用；真实渲染留待后续 E2E 批次。
- **包体积增长（低）**：按需引入后新增约数十 KB。处置：验收时检查构建产物，确认未整包引入。

## Migration Plan

1. 替换 `modules/usage/routes.ts` 占位组件为真实视图，同步 `route-matrix.spec.ts`。
2. 新增 `types.ts` / `api.ts`（2 端点，DRAFT 注释含字段来源行号）与图表组件。
3. 补 `src/mocks/browser.ts` 两段 handler（完整形态 + 缺失形态）。
4. 后端端点落地后：仅把 mock 门控关闭、按对齐结果微调字段映射；页面结构与交互不变。
5. 回滚：单模块目录 + 一条路由装配，删除即回到占位状态（可插拔反向验证已覆盖）。

## Open Questions

- DRAFT 契约待后端确认：`summary` 的 `failure_rate` 分母（全量 vs 非 rate_limited）、`trend` 的 bucket 边界（左闭右闭）、`avg_latency_ms` 是否排除 null 记录；`logs` 是否支持 `provider` 筛选。
- **status 枚举已知不一致（P1-1，已核实）**：`llm_log.py:25` 注释为 `success/failed/timeout/circuit_open`，qc-service 实际写入 `ok`（`llm_reviewer.py:466`）/ `qc_review_exemption`（:390/:407/:425/:436/:449/:459）/ `skipped`（:397）。前端按「原样透出、不做映射」实现；对外筛选口径与是否归一化待后端确认，未确认前筛选项以注释四值为草案。
- 后端是否新增 `llm_call_logs.svc` 列，或由 `session_id` / `trace_id` 前缀推导服务维度。
- 后端是否把指标族计数（rate_limited / breaker_open / fallback）透出到 `summary.resilience`，或另立用接口。
- complaint / rag 是否补 `llm_call_logs` 写入埋点与 token/latency 字段（决定看板何时能反映全量）。
- 「与 llm_call_logs 表 count 一致」退出标准是否修订为「qc 维度 count 一致」，待后端变更落地后复核。

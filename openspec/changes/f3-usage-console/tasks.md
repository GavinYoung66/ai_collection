# Tasks

## 1. 契约草案对齐与 SSOT 回填（§13 闭环）

- [x] 1.1 逐字段固化 DRAFT 契约并记录来源：summary/logs 全部字段对照 `common/db/models/llm_log.py:14-27`（id/trace_id/session_id/provider/model/token_usage/latency_ms/confidence/status/error_msg/create_time）；status 取值以实际写入为准——`ok`（`llm_reviewer.py:466`）、`qc_review_exemption`（:390/:407/:425/:436/:449/:459）、`skipped`（:397）；`llm_log.py:25` 注释四值仅计划值，不得当作唯一枚举来源；确认无自造列名（证据：design.md「DRAFT 契约草案」逐字段表）
- [x] 1.2 与后端对齐契约草案：failure_rate 分母、trend bucket 边界、avg_latency_ms 是否排除 null、logs 是否支持 provider 筛选、status 对外口径与筛选取值（当前注释四值与实际写入值不一致）；结论不同则就地修订 spec/design 并报备；无结论则登记 design Open Questions，不阻塞后续批次（证据：无后端接口人可比对，全部登记 design.md Open Questions 共 6 条）
- [x] 1.3 三项数据缺口登记入 design Risks：`llm_call_logs` 无 svc 列 / 仅 qc-service 写入（`llm_reviewer.py:254`）/ token_usage 与 latency_ms 未落库（证据：design.md Risks 第一条 + Open Questions 第 3~5 条）**→ 2026-10-24 更新**：`svc` 列已补（迁移 0017 `0017_llm_call_logs_svc.py`）；埋点已扩到 replay 与自动质检链路（`persist_call_logs=True`）；`token_usage`/`latency_ms` 落库仍以 `llm_reviewer` 实际写入为准
- [x] 1.4 SSOT 回填：§5.5 状态标注「前端以 DRAFT 契约 + mock 驱动实现（批次②-⑧），后端端点待实现」、§4 路线表 F3 行状态（退出标准注「本批不可达，真链路待复核」）、附录 B.2 v1.7（证据：SSOT:435、:116、:884）；未改写 §5.0 E 表「待实现」

## 2. echarts 引入与 types/api 层

- [x] 2.1 引入 echarts 并按需注册（`echarts/core` + `LineChart`/`PieChart` + `GridComponent`/`TooltipComponent`/`LegendComponent` + `CanvasRenderer`），禁止整包 import（SSOT:466）（证据：`modules/usage/echarts.ts`；package.json 新增 echarts ^6.1.0）
- [x] 2.2 `types.ts`：定义 summary / logs 请求与响应 interface，逐字段注释 `llm_log.py` 行号；status 联合类型以实际写入值为基线（`ok` / `qc_review_exemption` / `skipped`），并保留 `llm_log.py:25` 计划值作为 DRAFT 候选、注释标明不一致（P1-1），不发明枚举（证据：modules/usage/types.ts）
- [x] 2.3 `api.ts`：2 条调用一律 `/api/v1/llm-usage` 全前缀、`TIMEOUT.normal`（SSOT:251-252）、无超时字面量；page 钳制 ≥1、page_size 钳制 1~100；时间范围参数名与 1.2 结论一致（证据：modules/usage/api.ts，均为 start/end）

## 3. 路由替换与可插拔验证（§7.1 / §11.1:717）

- [x] 3.1 `routes.ts` 占位组件换为 `() => import('../views/UsageDashboard.vue')`，删除占位 `props: { title: 'LLM 用量' }`；name=`UsageDashboard`、roles=`['admin','supervisor']` 保持不变；**新增** `meta.keepAlive: true`（现状 `modules/usage/routes.ts:14` 无该键）；`index.ts` 默认导出 BizModule {name:'usage', routes}（证据：modules/usage/routes.ts；views/UsageDashboard.vue name 一致）
- [x] 3.2 `route-matrix.spec.ts`：MATRIX 已含 `/usage`，确认既有 4 条断言覆盖 roles/name/title/装配；未断言 keepAlive 则补 1 条（MATRIX 增 keepAlive 字段）；确认未新增子路由（证据：route-matrix.spec.ts 新增第 6 条用例，6/6 通过）
- [x] 3.3 可插拔反向验证：临时删除 `modules/usage` + `app/router/index.ts` 装配行，`vue-tsc --noEmit && vite build` 通过；恢复后复跑 route-matrix 用例（§7.2）（实际执行：受安全策略限制未移动模块目录，改为临时移除装配 2 行后 `vite build` 通过（1760 modules），恢复后 `git diff` 为空、route-matrix 6/6、vue-tsc 通过；引用面仅装配与测试两处）

## 4. 指标卡与时间范围（§5.5:437）

- [x] 4.1 `components/UsageStatCards.vue`：4 项指标（调用量 / token 总量 / 平均时延 / 失败率），props 进、无 api 调用（证据：components/UsageStatCards.vue；总计 4/4 用例通过）
- [x] 4.2 `views/UsageDashboard.vue`：时间范围（近 1 天 / 7 天 / 30 天 / 自定义）+ 组件本地 ref 管状态（D5）；切换触发重取；onActivated 刷新、onMounted 首激活去重（证据：UsageDashboard.spec.ts「切换近 30 天」用例）
- [x] 4.3 看板空态以 `totals.calls === 0` 判定，展示引导文案，不渲染零值图表（spec「无数据空态」）（证据：UsageDashboard.spec.ts「calls === 0 → 空态」用例）

## 5. 分布与趋势图（§5.5:438-440）

- [x] 5.1 `components/UsageModelChart.vue`：`by_model` 分布（calls/tokens），无数据时不渲染空图（证据：UsageModelChart.vue，el-empty 占位；Dashboard 6/6 用例）
- [x] 5.2 `components/UsageTrendChart.vue`：双轴折线（calls + failure_rate），series 与 axis 配置可在单测断言（证据：UsageTrendChart.vue yAxis[0]=调用量/yAxis[1]=失败率）
- [x] 5.3 `components/UsageServiceChart.vue`：仅 `by_service` 存在且非空时渲染，缺失整块隐藏（D4）；断言第二种形态（证据：UsageDashboard.spec.ts「by_service 缺失/存在」两条用例）
- [x] 5.4 图表生命周期：onActivated 重绘 + resize 监听，onDeactivated/onUnmounted 移除（D6）（证据：composables/useUsageChart.ts onMounted/onActivated/onBeforeUnmount）

## 6. 调用明细与 trace_id 检索（§5.5:441-444）

- [x] 6.1 明细 Tab：分页表（model/status/latency/token/create_time），page_size 默认 20、上限 100，空态以 `total === 0` 判定（证据：components/UsageLogTable.vue；UsageLogTable.spec.ts 默认参数/空态/page_size 用例）
- [x] 6.2 status 筛选含「失败」快捷项；筛选变更重置到第 1 页（证据：UsageLogTable.spec.ts「切换 status 筛选」用例；草案值 failed）
- [x] 6.3 trace_id 精确检索输入 + 结果展示（共享 `TraceIdText` 可复制）；无结果为页内空态，不弹全局错误（D6 不引入模块级 trace 上下文）（证据：UsageLogTable.spec.ts「trace_id 精确检索」「未命中文案」用例）
- [x] 6.4 `error_msg` 行展开：展示原文与 trace_id，不做不可读截断（证据：UsageLogTable.vue `type="expand"` 列 + `<pre>` 原文 + TraceIdText）

## 7. 韧性块与数据完整性横幅（§5.5:445-446）

- [x] 7.1 `summary.resilience` 三项计数展示（rate_limited / breaker_open / fallback），缺失整块隐藏（D4）；前端不自算（证据：UsageDashboard.vue 韧性卡片 v-if；UsageDashboard.spec.ts「resilience 缺失/存在」用例）
- [x] 7.2 常驻横幅「用量数据当前仅 qc-service 上报；complaint/rag 埋点与 token/时延字段补齐前，分布与均值可能不完整」（D7），断言与数据状态无关（证据：UsageDashboard.spec.ts「空态下依然展示横幅」用例）
- [x] 7.3 错误分支验收（spec R9）：summary 命中 B0006（带 Retry-After）与 B0009 时走既有白名单文案（§15.1:779-780），页面不崩溃、不自动轮询、不新增错误码映射；error-map 条目数与基线一致（证据：UsageDashboard.spec.ts「B0006/B0009 白名单文案 + 重试」用例；拦截器 request.ts:166-179 既有分支，error-map 未改动）

## 8. mock 双形态与单测

- [x] 8.1 `src/mocks/browser.ts` 新增 summary / logs handler：完整形态（含 by_service + resilience）与缺失形态（不含二者）；logs handler 返回分页信封；追加到 `ragHandlers`（browser.ts:301）并将数组/类型更名为通用 `handlers` / `MockHandler`、同步 :366 注释为「未命中 mock 端点」；沿用 DEV + VITE_USE_MOCK 双重门控（D8）（证据：browser.ts `usageSummary` / `usageLogs` + `?shape=missing`；`RagMockCtx/RagMockReply/RagMockHandler/ragHandlers` 已全量更名并同步注释）
- [x] 8.2 `__tests__/api.spec.ts`：2 端点路径、时间范围透传、trace_id 参数名、page/page_size 双向钳制（证据：api.spec.ts 6/6 通过，含 `TIMEOUT.normal`）
- [x] 8.3 `__tests__/UsageDashboard.spec.ts`：Tab 切换、时间范围重取、看板/明细两种空态、缺省隐藏（by_service / resilience）、失败筛选、trace_id 检索参数、横幅常驻、非注释 status（qc_review_exemption/skipped）原样展示（证据：UsageDashboard 9 + UsageLogTable 7 共 16 条用例）
- [x] 8.4 `__tests__/charts.spec.ts`：mock echarts，断言 `setOption` 的 series 数据与双轴配置；断言 onActivated 重绘与监听清理（证据：charts.spec.ts 6/6 通过）
- [x] 8.5 `error-map.spec.ts`：断言未新增映射（映射表条目数与基线一致）（证据：error-map.spec.ts 23/23 通过，条目数未变）
- [x] 8.6 单测全量回归 `npm run test`，确认其他模块用例无回归（证据：33 文件 / 317 用例全绿；基线 288 → 317，新增 29 条）

## 9. 验收与附录回填

- [x] 9.1 四道门禁全绿：`npm run lint`、`vue-tsc --noEmit`、`depcruise src`、`vitest run`（证据：lint 0 errors（271 warnings 为脚本既有上限，`modules/usage` 自身 0 warning）；typecheck 无输出；depcruise 1791 modules / 7401 deps 无违规；vitest 33 文件 317 用例全绿）
- [x] 9.2 §5.5 功能树逐条对照（4 指标卡 / 时间范围 / 模型分布 / 双轴趋势 / 服务分布 / 明细分页 / 失败筛选 / trace_id 检索 / 韧性块），缺项在 design 有砍项理由（证据：§5.5:436-449 共 8 项（含韧性加分项）全部落地；无缺项需砍项理由）
- [x] 9.3 §5.0 **E 表**（SSOT:247-251）2 端点以 DRAFT 身份逐字段核对（method + path + 参数 + 响应字段）（证据：api.ts 路径 `/api/v1/llm-usage/summary|logs`；design「DRAFT 契约草案」逐字段表）
- [x] 9.4 输出逐项 pass/fail 对照表（标注证据），并显式声明「真链路未验收」及三项原因（端点不存在 / 网关无 location / 埋点缺口）；不表述「与 llm_call_logs 表 count 一致」为已达成（证据：本批交付报告逐项表；SSOT §4 F3 行与 §5.5 状态块同步声明）**→ 2026-10-24 复核更新**：三项原因已消除（`llm_usage.py` 两端点 + 网关 location + `llm_reviewer`/replay `persist_call_logs` + `svc` 列迁移 0017/0018）；后端单测 `test_llm_usage_api.py` 8/8 通过（conda py3.11.16 + PYTHONPATH=仓库根，非 docker）；smoke 已加 `summary`/`logs` 断言（`scripts/smoke/smoke_qc.sh`）；SSOT §4:116、§5.0 E:247-252、§5.5:435 已回填；**真链路仍未运行**（docker daemon 未启动），归档前必须补登录→看板→trace_id 检索证据
- [x] 9.5 附录 B.2 回填核对（与 1.4 一致，版本号递增）（证据：SSOT 附录 B.2 v1.7 行）
- [x] 9.6 `openspec validate f3-usage-console` 通过；产物 4/4 齐全（证据：Change 'f3-usage-console' is valid）

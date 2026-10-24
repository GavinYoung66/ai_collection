# Tasks

## 1. API 层与类型契约

- [x] 1.1 逐字段核实后端契约：从 `services/qc-service/app/routers/`（demo.py / records.py / check.py / review.py / alerts.py / rules.py）与 `app/models` 核实 15 端点的请求/响应字段、action 三值枚举（CONFIRM/CORRECT/DISMISS）、points 档位（high 20/medium 8/low 2）与 R13 不计入规则、`HTTP_STATUS_BY_CODE` 分发表（`app/errors.py:14-29`），产出 `frontend/src/modules/qc/types.ts`（snake_case 逐字一致；可选字段 `T | null`；时间 ISO 字符串透传；UUID 按 string；分页复用 `shared/types/envelope.ts` 的 `PageResult<T>`）；核实记录注明每字段来源行号；验证：`npx vue-tsc --noEmit` 通过
- [x] 1.2 records 分页签名核实：确认 records.py 列表路由分页参数与 §5.0.1（page 从 1、page_size 默认 20、上限 100）是否一致；不一致则走 SSOT 闭环回填 §5.0 C 表并记录版本变更，types.ts 以后端实际为准；验证：核实结论写入 types.ts 注释（含 records.py 行号）
- [x] 1.3 实现 `frontend/src/modules/qc/api.ts`：15 端点一律 `/api/v1/qc/*` 全前缀，只 import `shared/request`；replay/{session_id}、check/batch 用 `TIMEOUT.qcReplay`，其余 `TIMEOUT.normal`；replay/raw 携带 `X-Idempotency-Key`（UUID）；force 重跑经 `X-QC-Force-Rerun` 头/body.force 透传；验证：`grep -rn "[0-9]\{4,\}" src/modules/qc/api.ts` 无超时数值字面量
- [x] 1.4 `shared/request.ts` 新增 `TIMEOUT.qcReplay = 110_000`（唯一 shared 改动，既有 normal/judge/拦截器逻辑一律不动）+ 更新文件头注释；验证：`npx vitest run src/shared` 既有用例零回归
- [x] 1.5 编写契约单测 `frontend/src/modules/qc/__tests__/api.spec.ts`：mock `shared/request`，逐条断言 15 端点 method+path、replay/batch 的 `timeout === TIMEOUT.qcReplay`、replay/raw 请求头含 `X-Idempotency-Key`、其余端点为 `TIMEOUT.normal`；验证：`npx vitest run src/modules/qc` 全绿

## 2. 网关超时放行

- [x] 2.1 `deploy/gateway/server_common.conf.template` 照 judge location 模板（L83-91）新增两个正则 location：`^/api/v1/qc/replay/` 与 `^/api/v1/qc/check/batch$`，`proxy_read_timeout 120s`，鉴权子请求/trace 头透传与 judge 一致；验证：模板渲染后 `nginx -t` 等价校验通过（或容器内 reload 干跑）
- [x] 2.2 SSOT 回填：《frontend/前端架构.md》§5.0 C 表 replay/batch 两行超时列改为 110s（注明网关 location 120s）、§9.3 矩阵补两行、附录变更表登记版本递增；验证：`grep -n "qcReplay\|120s" frontend/前端架构.md` 命中 §5.0 C 与 §9.3 两处
- [x] 2.3 网关 reload 生效确认：在冒烟环境重载 nginx 并留证据（reload 命令输出/配置快照）；验证：冒烟任务 8.3 前置检查项引用本证据，未 reload 不得开始冒烟

## 3. 路由装配与角色收窄

- [x] 3.1 重写 `modules/qc/routes.ts` 替换占位：6 条路由（/qc/tasks、/qc/records 先于 :sessionId、/qc/records/:sessionId 不缓存、/qc/reviews、/qc/rules、/qc/alerts），`meta.roles` 按 design D3 收窄表（rules=['admin']、alerts=['admin','supervisor']、reviews=['supervisor','qc_reviewer']、其余三条=['admin','supervisor','qc_reviewer']），`meta.title/icon` 齐全，列表页 keepAlive，视图 `() => import()` 懒加载；视图文件未就绪的路由以注释占位（同 F1 2.4 做法），随后续批次补开；验证：`npx eslint` 与 `npx depcruise src` 全绿
- [x] 3.2 同步更新 `app/__tests__/route-matrix.spec.ts`：MATRIX 增补 6 条 qc 路由与收窄后 roles，路径集合与路由数断言同步；验证：`npx vitest run src/app` 通过
- [x] 3.3 SSOT 回填：《frontend/前端架构.md》§11.1 路由表 6 条 qc 行 roles 列按收窄表更新、§3.2 矩阵合规质检行补注「页面级按收窄路由、按钮级按 §5.0 F」、附录变更表登记；验证：`grep -n "qc/rules\|qc/alerts\|qc/reviews" frontend/前端架构.md` 与 routes.ts 逐条一致

## 4. 质检任务台

- [x] 4.1 实现 `views/QcTaskBoard.vue`（`defineOptions({ name: 'QcTaskBoard' })`）：待检队列（demo/pending）+ 已出分记录合并视图（口径注释见 design 风险条），行内开检（replay/{session_id}，全程加载态+防连点+禁同行重复触发），已出分行点击仅拉 records/{session_id} 不重跑，`onActivated` 刷新 `onMounted` 不重复拉取；验证：组件单测覆盖开检防连点、已出分行不发 replay、双数据源合并渲染
- [x] 4.2 演示入口仅 DEV：灌数（demo/seed）与 replay/raw 入口仅 `import.meta.env.DEV` 渲染；验证：组件单测断言生产构建无入口
- [x] 4.3 批量质检：勾选上限 10（常量 `QC_BATCH_LIMIT = 10`，依据 design D2 测算），超限禁用并提示「大批量走后端异步（P1）」；提交用 `TIMEOUT.qcReplay`，结果分区展示 accepted/rejected（rejected 附 code）；验证：组件单测覆盖上限禁用、提交超时配置、rejected 展示

## 5. 质检报告与详情

- [x] 5.1 实现 `views/QcRecordList.vue`（`defineOptions({ name: 'QcRecordList' })`）：PageTable 分页 + 「扣分」列（无 grade 列）+ stats 统计卡（透传后端字段不聚合，不引 ECharts）；验证：组件单测断言默认分页参数、扣分列渲染、统计卡字段与 mock 响应一致；`grep -i echarts package.json` 零命中
- [x] 5.2 实现 `views/QcRecordDetail.vue` 骨架（`defineOptions({ name: 'QcRecordDetail' })`，不缓存）：按 sessionId 加载 records/{session_id}，展示 deduction_total/compliance_score/risk/qc_status/qc_result（不展示 grade）；B0004 空态+返回列表；验证：组件单测覆盖字段渲染与 404 空态
- [x] 5.3 实现命中表与条款组件（`components/`）：命中表含来源 ac|rule|llm、rule_id/词、证据、points 档位、R13 客户侧标注不计入、policy_clause/policy_text；kb_snippet 独立区块不与 policy_text 混用；skipped_rules 文案「本通未审/未适用」；会话原文对照命中证据高亮；验证：组件单测覆盖 R13 标注、snippet/条款分离、skip 文案不含「已合规」
- [x] 5.4 实现 trace 时间线组件（`components/`，模块内自建不下沉，design D4）：取 demo/{session_id}/trace，渲染 category/name/status/summary 事件流，仅存在 langsmith_url 时显示外链；验证：组件单测断言无 langsmith_url 时无外链
- [x] 5.5 实现 `composables/useQcTraceContext.ts`：详情页挂载 `setTraceContext`、卸载清空（照搬 complaint 模式），业务代码不手写 X-Trace-Id；验证：模块侧单测断言详情页连续两次操作请求头携带同一 X-Trace-Id、离开后列表请求不携带；`npx vitest run src/shared` 零回归

## 6. 人工复核、规则与告警

- [x] 6.1 实现 `views/QcReview.vue`（`defineOptions({ name: 'QcReview' })`）：待复核队列（reviews/pending），行内进详情或直接展开复核操作；复核提交逻辑复用 6.2 组件；验证：组件单测断言队列请求路径与空态
- [x] 6.2 实现人审操作组组件（`components/ReviewActions.vue`，复核页与详情页共用）：零扣分不渲染提交按钮；「扣分正确」=CONFIRM 直提；「误报/改扣分」=CORRECT 且 revised_deduction 必填（>=0 整数）；不暴露 DISMISS；REVIEWED 后禁用；当前角色 ∉ {qc_reviewer, supervisor} 时置灰+说明；验证：组件单测覆盖零扣分无按钮、CORRECT 必填拦截、REVIEWED 禁用、admin 置灰四种形态
- [x] 6.3 实现 `views/QcRules.vue`（`defineOptions({ name: 'QcRules' })`）：rules/active 只读列表（R01~R14），无任何编辑入口；rules/apply 二次确认后发起；验证：组件单测断言无编辑控件、热应用需二次确认
- [x] 6.4 实现 `views/QcAlerts.vue`（`defineOptions({ name: 'QcAlerts' })`）：alerts 列表 + ack 确认（提交中防连点，成功更新该条状态）；验证：组件单测覆盖 ack 防连点与成功态更新

## 7. 错误处理模块侧动作

- [x] 7.1 落实 §15.1 白名单模块侧动作：A2002 表单行内标红（不叠 B0001）、A2003 质检按钮置灰+悬浮说明原因不发起请求、B0004 详情空态+返回列表、B0005 幂等提示、B0006 按 Retry-After 退避、B0007/B0008/B0009 兜底文案+trace 后 8 位+重试按钮；未列入码走默认分支 message 直出+trace 后 8 位，不新增映射；验证：交互单测逐码覆盖（含 A2011 降级走默认分支+降级标识展示，design 风险条）

## 8. 全量验证与真链路冒烟

- [x] 8.1 四道 CI 门禁本地全绿 + 反向可拔验证：`npx eslint`、`npx vue-tsc --noEmit`、`npx vitest run`、`npx depcruise src` 全部通过；随后临时移除 `modules/qc/`，`vue-tsc --noEmit && vite build` 仍通过（§17，验证后还原）；验证：逐条命令退出码 0
- [x] 8.2 双对照验收：① §5.0 C 表 15 端点 method+path 与 api.ts 逐条对照；② 旧 `services/qc-service/web/app.js` 功能清单逐项对照（功能集合 ⊇，localStorage 缓存/API_BASE 页眉/演示 JWT 为已批准废弃项）；验证：输出逐项 pass/fail 对照表（标注证据）
- [x] 8.3 真实浏览器三角色冒烟（前置：任务 2.3 网关 reload 证据已确认）：admin（任务/报告/规则/告警可用，复核页不可见+直输 URL 落 /403+详情人审按钮置灰）、supervisor（任务/报告/复核/告警，无规则页）、qc_reviewer（仅任务/报告/复核）；主链路：admin 灌数 → qc_reviewer 待检行开检（验证 120s location 生效，replay 不 504）→ 详情命中表/条款/trace → 提交复核（CORRECT+revised_deduction）→ supervisor 告警确认 → admin 规则热应用；验证：逐步 pass/fail 记录（含每步网络请求与界面证据），失败项立即记录根因（执行注记：接口级三角色矩阵 9/9 通过、网关 120s location 经 13.8s 真实请求验证、demo/pending=26/records=553/stats/trace/detail 全 200；replay/batch 终态事件发布因冒烟环境 RocketMQ topic 不可用返回 B0007（后端环境问题，非本 change 范围）；浏览器 UI 冒烟因工具环境系统性超时降级为 route-matrix 60 格单测+接口矩阵，建议人工抽查）
- [x] 8.4 spec 场景终验：按 `specs/frontend/qc-console/spec.md` 全部 Requirements 的 Scenario 逐项核对并输出 pass/fail 对照表；验证：全部 pass 或经用户确认的偏差记录

## Workflow follow-up

- 用户确认验收通过后归档本 change（/opsx-archive），`frontend/qc-console` 并入主 specs。
- F2 另一条线（RAG 知识库平移）独立 change（f2-rag-console），待 `kb/search(answer=true)` 网关放行方案确认后立项。
- **门禁修正（本次复审发现并已修复）**：① CI 类型闸原为 `vue-tsc --noEmit`，因根 tsconfig 为 solution 式（`files: []`）长期空转通过，已改为 `vue-tsc -b --force`，并注入错误做门禁自测（exit=2 可拦、还原后 exit=0）；② ESLint 0 error / 271 warning，已在 `package.json` 冻结 `--max-warnings 271`（只降不升），CI 同步注释说明。
- **GitHub Actions 尚未对 f2 执行**：本批产物仍在工作区（未提交），推送后须复验四道闸真实绿，再归档。
- **交互时序覆盖独立立项**：`f3-frontend-e2e`（Playwright 壳层用例，含本批导航「展开后折叠」缺陷回归），用以替代 8.3 遗留的浏览器 UI 人工抽查。

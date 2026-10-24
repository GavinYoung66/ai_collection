# Tasks

## 1. API 层与类型契约

- [x] 1.1 逐字段核实后端契约：从 `services/complaint-service/app/api/routes/complaints.py`、`stats.py`、`mode.py` 及 `app/models/state.py` 核实 16 端点的请求/响应字段、`task_status`（含终态与等待人工态集合）/ `verdict` / `review_action` / 定责三值枚举与 stats 返回结构，产出 `frontend/src/modules/complaint/types.ts`（snake_case 逐字一致；可选字段 `T | null` 不用 undefined；时间 ISO 字符串透传；UUID 按 string；分页复用 `shared/types/envelope.ts` 已存在的 `PageResult<T>`（不新增、不改名））；验证：`npx vue-tsc --noEmit` 通过，核实记录注明每字段来源行号
- [x] 1.2 实现 `frontend/src/modules/complaint/api.ts`：16 端点全部走 `/api/v1/complaints/*` 全前缀，只 import `shared/request`，超时只引用 `TIMEOUT` 常量（judge 用 `TIMEOUT.judge`，其余 `TIMEOUT.normal`）；验证：`grep -rn "api/v1/stats\|api/v1/mode" src/modules/complaint` 零命中、`grep -rn "[0-9]\{4,\}" src/modules/complaint/api.ts` 无超时数值字面量
- [x] 1.3 编写契约单测 `frontend/src/modules/complaint/__tests__/api.spec.ts`：mock `shared/request`，逐条断言 16 端点 method+path、列表默认参数 `page=1&page_size=20`、page_size 上限 100 钳制、judge 请求 `timeout === TIMEOUT.judge`；验证：`npx vitest run src/modules/complaint` 全绿
- [x] 1.4 后端补列表状态过滤（实施期发现的 spec-vs-backend 冲突，2026-10-23 经用户批准方案 A，属「禁止改后端」边界的唯一例外）：`complaints.py` 列表路由补 `task_status: Optional[list[TaskStatus]] = Query(default=None)` 重复查询参数 + `complaint_service.list()` 集合过滤（先于分页计数）+ `tests/unit/test_api_routes.py` 新增 3 条（单值过滤/多值并集/非法值 422）；前端契约同步：`ComplaintListQuery.task_status?: TaskStatus[]`、api.ts 透传且 `paramsSerializer.indexes:false` 匹配 FastAPI 重复参数、契约单测 +1 条；SSOT §5.0 B 表回填（v1.3）。验证：容器内 `pytest tests/unit/test_api_routes.py -k "list or create"` 4 条全绿；前端四道门禁全绿。注：`test_trace_after_run_graph_has_agent_and_llm` 失败经 git stash 验证为既有问题，与本改动无关

## 2. PageTable 下沉与列表页

- [x] 2.1 下沉 `frontend/src/shared/components/base/PageTable.vue`：纯 props/emit（columns/rows/total/loading，page 默认 1、pageSize 默认 20、上限 100），内置 loading/empty/error 三态，rows 对应分页响应的 `items`（复用 `shared/types/envelope.ts` 的 `PageResult<T>`），不含业务字段、不 import store/modules；配套单测断言默认值、三态与事件；验证：`npx vitest run src/shared` 通过且 depcruise 无 `shared → modules/app` 违规
- [x] 2.2 实现 `views/ComplaintList.vue`（`defineOptions({ name: 'ComplaintList' })`）：PageTable 分页 + 状态筛选（枚举取 types.ts）+ 行内「研判/详情」+ 建单弹窗（提交中防连点）+ 演示灌数/事件入口仅 `import.meta.env.DEV` 渲染；验证：组件单测覆盖默认请求参数、行内研判只触发当行、建单防重复；`onActivated` 刷新 `onMounted` 不重复拉取
- [x] 2.3 实现 `views/ComplaintReview.vue` 薄包装（`defineOptions({ name: 'ComplaintReview' })`）：复用列表组件，固定待复核状态集合（实现期以 `state.py` 为准，至少含 `pending_review`），筛选控件锁定；验证：组件单测断言请求仅命中待复核状态集合且集合与 types.ts 逐字一致
  - **2026-10-23 修订（经用户批准，spec 同步改为「复核工作台必须是独立的左右分栏待办页」）**：实测「复用列表 + 锁筛选」与投诉列表视觉无从区分，用户两次反馈「两个子路由点进去是同一个页面」。已重写为独立分栏页——左栏待复核队列（固定状态集合、无筛选控件）、右栏原地完成定责/复核/回电（不跳详情页），另加「打开完整详情」跳转供查看 Trace/Agent 日志。同步移除列表页仅为复核页服务的 `fixedStatusFilters`/`filterLocked`/`pageTitle` 三个 prop。验证：`review.spec.ts` 6 条（队列固定集合+不复用列表、无接入侧操作、原地填充不跳页、中文复核动作、按状态收敛、定责提交传原值）+ 浏览器实测通过
- [x] 2.4 重写 `modules/complaint/routes.ts` 替换占位：`/complaints`、`/complaints/review`（声明在 `:id` 之前）、`/complaints/:id` 三条，`meta.roles` 均为 `["admin","supervisor","agent"]` 且 `meta.title/icon` 齐全，列表两条 keepAlive、详情不缓存，视图组件一律 `() => import()` 懒加载；**注：`:id` 路由因 `ComplaintDetail.vue` 本批未存在导致 vue-tsc TS2307 模块找不到，延后到 3.1 与详情视图同批落盘，本批 routes.ts 以注释保留占位**；验证：同步更新 `app/__tests__/route-matrix.spec.ts`——MATRIX 增补 `/complaints/review`、`complaintRoutes.length` 断言改为 2（active 路由数，`:id` 注释），路径集合断言同步，之后 `npx vitest run src/app` 通过，`npx eslint` 与 `npx depcruise src` 全绿，构建产物中 complaint 视图为独立 chunk

## 3. 详情页

- [x] 3.1 实现 `views/ComplaintDetail.vue` 骨架：按 id 加载 `GET /{id}`，渲染状态步骤条（task_status 枚举取 types.ts，前端不发明状态）、AI 初选与人审定责对照区、结论卡（`GET /{id}/verdict`）；验证：组件单测覆盖 judging 高亮与改判对照展示
- [x] 3.2 实现详情 Tab 组（`components/`）：证据包（详情响应 `ordered_evidence` + `evidence_refs`，支持 RAG 源 ID 回溯）、trace（`GET /{id}/trace` 事件时间线）、agent 日志（`GET /{id}/agent-logs`）、草稿；各 Tab 首次切换才发请求；若需抽屉形态则同步下沉 DetailDrawer，否则不建；验证：组件单测断言未切换的 Tab 不发请求、证据条目含源 ID 回溯信息
- [x] 3.3 实现 `composables/usePolling.ts`，严格满足 §14.1：首轮 3s、连续 20s 无变化退避 5s、上限 10s；`task_status ∈ {resolved, closed, archived}` 停止并补拉一次 `/verdict` 与 `/agent-logs`；等待人工态低频继续或停止并提示；`document.hidden` 暂停、`visibilitychange` 恢复补一次；连续 3 次失败熔断 + trace 后 8 位；`onUnmounted` 清理；禁止轮询详情端点；验证：fake timers 单测覆盖退避阶梯、终态停止补拉、可见性暂停恢复、3 次失败熔断、卸载清理
- [x] 3.4 实现操作组：judge（`TIMEOUT.judge` await + 全程防连点，请求成败仅提示、不控制轮询）、定责确认（三选一，与 AI 不一致时改判原因必填，禁止提交 pending）、回电登记（`B0005` 幂等提示）、人工复核（review_action 六值与后端逐字一致）、关单（前置失败原地展示 message + trace 后 8 位）、重新研判；落实模块侧错误动作：`A5001` 详情 404 空态 + 返回列表、`A5002` 自动刷新详情、`B0010` 强提示不重试、`A5015/A5016/A5017` 正向提示跳复核队列；验证：交互单测覆盖防连点、改判必填拦截、关单失败提示含 trace 后 8 位、四类错误码动作
- [x] 3.5 X-Trace-Id 注入与详情页上下文（§9.4 F1 落地，本批唯一 shared 既有文件改动）：① 在 `shared/request.ts` 请求拦截器内补注入——`getTraceContext()` 非空时 `config.headers.set('X-Trace-Id', ctx)`，**Bearer/信封解析/401/429/超时等既有逻辑一律不动**；② `composables/useTraceContext.ts` 在 `ComplaintDetail` 挂载时经 `setTraceContext` 写入、卸载时清空；业务代码不手写该头。验证：`shared/__tests__/request.spec.ts` 既有 11 条用例全部仍通过（无回归）+ 新增 2 条断言（有上下文时注入、无上下文时不注入）；模块侧单测断言详情页连续两次操作请求头携带同一 `X-Trace-Id`、离开后列表请求不携带

## 4. 统计与模式切换

- [x] 4.1 列表页统计卡片区：`GET /complaints/stats` 以基础组件呈现后端返回字段，不自行聚合；验证：组件单测断言渲染字段与 mock 响应一致，`grep -i echarts package.json package-lock.json` 零命中
- [x] 4.2 模式切换双签交互：二次确认 + 理由输入（理由为空禁止确认）后调 `PUT /complaints/mode`，仅展示返回的切换后模式，页面无「当前模式」读取请求与常驻展示位；`A5014` 以 ElMessageBox 强警示并注明「当前模式查询待后端补端点」（§15.1 与 §5.2 的 SSOT 内部矛盾按此降级）；验证：交互单测覆盖理由空禁用、成功后只展示切换结果、A5014 强警示文案
  - **2026-10-23 修订（经用户批准，spec 同步改为「运行模式切换为灰度运维能力，业务页面不得暴露入口」）**：实测该按钮被用户误认为「AI Agent 模式」，且实施期发现三个实情（reason 未随请求下发——后端 `ModeSwitchRequest` 无该字段；`signers` 前端硬编码 `['ops-operator','tech-lead']`，双签未真正校验到人；后端无 `GET /mode` 无法回读）。已下线整个入口与弹窗，删除 `mode-switch.spec.ts`（5 条用例全部针对该弹窗）。**后端接口与 `api.ts` 的 `switchComplaintMode` 保留**，供将来运维后台复用，契约单测仍覆盖该端点

## 5. 全量验证与真链路冒烟

- [x] 5.1 四道 CI 门禁本地全绿 + 反向可拔验证：`npx eslint`、`npx vue-tsc --noEmit`、`npx vitest run`、`npx depcruise src` 全部通过；随后临时移除 `modules/complaint/` 与装配行，`vue-tsc --noEmit && vite build` 仍通过（§17，验证后还原）；验证：逐条命令退出码 0
- [x] 5.2 双对照验收：① §5.0 B 表 16 端点 method+path 与 api.ts 逐条对照；② 旧 `services/complaint-service/web/app.js` 功能清单逐项对照（功能集合 ⊇，pending 自动研判为已批准废弃项）；验证：输出逐项 pass/fail 对照表（标注证据）
- [x] 5.3 真实浏览器冒烟：登录 admin → 手工建单 → 列表检索 → 进详情 → judge → status 轮询推进到 pending_review → 人工复核（review_action=approve）→ 定责确认 → 回电登记 → 关单 → 统计区有数（若后端状态机要求先定责后复核，按实际报错调整顺序并记录偏差，不臆造状态）；验证：逐步 pass/fail 记录（含每步网络请求与界面证据），失败项立即记录根因
- [x] 5.4 spec 场景终验：按 `specs/frontend/complaint-console/spec.md` 16 条 Requirements 的 Scenario 逐项核对并输出 pass/fail 对照表；验证：全部 pass 或经用户确认的偏差记录
  - 终验结果：14 条完全 pass；2 条因用户指令改写实现后已回填 spec（复核工作台→独立分栏页、模式切换→业务页不暴露入口），改后同样 pass。最终门禁：`eslint` 0 error、`vue-tsc` exit 0、`vitest` 16 文件 / 131 测试全绿、`depcruise` 零违规、`vite build` 成功、后端 `pytest` 385 passed / 44 skipped
  - 已知缺口（非本批缺陷，留待 F2 或后端批次）：① 定责前 `ordered_evidence` 为空是后端既定契约（`tests/unit/test_api_routes.py:454` 锁定），前端改为展示 trace 取证链 + 提示文案；② 演示环境 trace 为 stub（`biz.get_case` mock、RAG 固定命中 `rag_doc_001`），真实链路待接入；③ 待人工态仍按 5s 轮询 `/status`（可收窄为仅 `judging` 轮询，待用户决定）

## Workflow follow-up

- 变更边界（超出需先征得用户同意）：只允许改 `frontend/src/modules/complaint/**`、`frontend/src/shared/components/base/PageTable.vue`（新建）、`frontend/src/shared/request.ts`（仅请求拦截器注入 X-Trace-Id 一处）、`frontend/src/app/__tests__/route-matrix.spec.ts`（路由基线同步）、`frontend/src/shared/__tests__/request.spec.ts`（新增 2 条注入断言）、`frontend/vitest.config.ts`（批次 2 修复 vitest CommonJS 下 template 里 `import.meta.env` 解析失败的 define 替换）；禁止改后端、网关、其他四模块、`app/router/index.ts` 装配行。
- **边界追加（2026-10-23，均经用户明确指令，逐项登记）**：
  1. `frontend/src/app/layouts/AdminLayout.vue`——侧边栏父级菜单重构：`el-sub-menu` 分组分支 + 点击父级标题整行跳转首个可见子路由（用户需求「投诉研判需要有父级一级菜单」「点父级要跳到 /complaints」）
  2. `frontend/src/shared/components/base/PassthroughLayout.vue`（新建）——嵌套路由的透传壳，仅渲染 `<router-view />`
  3. `frontend/src/modules/complaint/routes.ts`——3 条扁平路由改 1 父级 + 3 children（详情页 `meta.menu: false`），最终 URL 与原扁平路径完全一致
  4. `frontend/eslint.config.js`——对 `**/*.{ts,tsx,vue}` 关闭 `no-undef`（TS 下由 tsc 负责，`lib.dom` 类型如 `MouseEvent/HTMLElement` 会被 ESLint 误报为未定义）
  5. `frontend/src/modules/complaint/views/ComplaintDetail.vue`——新增「AI 产出物」区块（AI 取证链 / 违规清单 / 应答草稿 + 复制）、Tab 标签带条数与 ✓、`?focus=judge|review|view` 锚点定位、操作区按 task_status 收敛
  6. `services/complaint-service/app/service/complaint_service.py` + `tests/unit/test_api_routes.py`——研判完成时写 `ai_verdict` 快照（原仅定责时回填，导致「AI 初选」恒显示「—」），属「禁止改后端」例外，经用户批准；测试用例按新语义同步 `ai_verdict` 赋值
  7. 删除 `frontend/src/modules/complaint/__tests__/mode-switch.spec.ts`（对应功能下线）
- 全程禁止 git add/commit/push；所有变更保持工作树状态，等待人工审查。
- 验收通过后由用户指令归档 change（/opsx-archive）。

# Design

## Context

F0 底座已归档：`shared/request.ts`（唯一 axios 实例、`TIMEOUT = { normal: 12_000, judge: 110_000 }`、信封解包、error-map 白名单、trace 后 8 位）、路由守卫与角色矩阵、`shared/components/base/`（ModulePlaceholder/StatusTag/TraceIdText）均可用；`modules/complaint/` 当前仅 `index.ts` + 占位 `routes.ts`。后端 16 端点已上线（SSOT §5.0 B 表含行号证据），本 change 不改后端/网关。需求与动机见 proposal.md；行为契约见 specs/frontend/complaint-console/spec.md。

## Goals / Non-Goals

**Goals:**
- `modules/complaint/` 满足 BizModule 自包含契约（index/routes/api/types/views/components），替换占位路由。
- 16 端点逐条接入，列表/详情/复核工作台/统计/模式切换全部可用；judge 长请求与 status 轮询体验闭环。
- PageTable 首次下沉 `shared/components/base/` 并被列表页消费。

**Non-Goals:**
- 不改后端、网关、`app/` 装配机制与其他四模块；不引 ECharts/SSE/WebSocket。
- 不做按钮级角色限制（后端角色鉴权为 P1 待补项，SSOT §11.1 权限边界声明）。
- openapi.json 契约漂移 CI（SSOT §13）不在本批，列为后续候选。
- 旧演示页 `services/complaint-service/web/` 的删除不在本 change（保留作对照基线，删除另议）。

## Decisions

### D1 复核工作台 = 薄包装路由复用列表组件
`/complaints/review` 渲染 `ComplaintReview.vue`（薄包装：`defineOptions({ name: 'ComplaintReview' })`，内部渲染列表组件并传固定待复核状态集合、锁定筛选控件；状态集合实现期以 `app/models/state.py` 为准，至少含 `pending_review`）。
- **理由**：SSOT §11.1 要求组件 `defineOptions.name` 与路由 name 逐字一致，keepAlive 按组件名缓存；若两路由共用同一组件实例名将冲突。薄包装既满足命名约束，又避免复制列表逻辑。
- **备选**：独立实现复核页 → 否决，重复维护两份列表/分页逻辑。

### D2 judge 与轮询的协作模型（职责分离）
judge 为同步长请求：点击后 `await api.judge(id)`（`TIMEOUT.judge`），期间按钮 loading 防连点；**judge 请求的成败仅影响提示，不控制轮询**。轮询生命周期只由 `task_status` 驱动：详情加载或操作后若 `task_status=judging` 即启动 status 轮询，严格满足 §14.1 契约——首轮 3s、20s 无变化退避 5s、上限 10s；`{resolved, closed, archived}` 停止并补拉一次 `/verdict` 与 `/agent-logs`；等待人工态由页面决定低频继续或停止并提示；`document.hidden` 暂停、`visibilitychange` 恢复补一次；连续 3 次失败熔断 + trace；`onUnmounted` 清理。轮询封装为模块内 `usePolling` composable，刷新/深链进入按 `task_status` 恢复。
- **理由**：SSOT §14.1 是退避+熔断模型而非固定 3s；judge 失败（如网关 504）时后端可能仍在跑图，轮询若随 judge 停止会丢失进度恢复能力，故二者职责必须分离。
- **备选**：SSE/WebSocket → 否决（后端无通道，且本 change 明确不引入）；固定 3s 轮询 → 否决（与 §14.1 退避契约冲突）。

### D3 统计与模式切换落在列表页工具区，不新增路由
`GET /stats` 以卡片区呈现于列表页顶部；模式切换入口放列表页工具区，双签交互（第一步确认弹窗 → 第二步理由输入 + 确认）后调 `PUT /mode`，仅展示返回的切换后模式。
- **理由**：SSOT §11.1 路由表无统计/模式独立路由；新增路由会偏离 SSOT。`GET /mode` 为后端待补端点，缺失时不展示当前模式（spec 已约束）。
- **备选**：独立 `/complaints/stats` 页 → 否决，与 SSOT 路由表冲突。

### D4 PageTable 下沉契约（F0 预留坑位）
`shared/components/base/PageTable.vue`：纯 props/emit 驱动（columns、rows、total、loading、`page` 默认 1、`pageSize` 默认 20、pageSize 上限 100），emit `update:page` / `update:pageSize`；内置 loading/empty/error 三态；rows 对应分页响应 `{items,total,page,page_size}` 中的 `items`，列表数据类型复用 `shared/types/envelope.ts` 已存在的 `PageResult<T>` 泛型（不新增、不改名）；不含任何业务字段、不 import store/modules。DetailDrawer 仅当详情 Tab 实际需要抽屉形态时才下沉，否则不建。
- **理由**：F0 spec「基础组件」要求 PageTable/DetailDrawer 在首次使用期补齐；默认值与后端分页契约（page≥1、page_size 默认 20、上限 100）一致可防双源漂移；三态是 §15 每页硬性要求，内置可避免各列表页重复实现。

### D5 X-Trace-Id 详情页复合链路（§9.4 F1 落地）
F0 现状是「只预留 `setTraceContext/getTraceContext` 读写函数，请求拦截器不注入」。本批 MUST 在 `shared/request.ts` 请求拦截器内补一行注入逻辑（存在 `getTraceContext()` 时 `config.headers.set('X-Trace-Id', ...)`），**这是本 change 唯一一处 shared 层既有文件改动，且仅限该拦截器**；Bearer、信封解析、401/429、超时等既有逻辑一律不动。`ComplaintDetail` 挂载时由模块内 composable 生成/写入 trace 上下文，卸载时清空；列表页不写入（无复合链路）。业务代码不手写该头。
- **理由**：§9.4 明确「F0 仅留扩展点，F1 投诉详情页落地」，而落地必然要求拦截器具备注入能力；若不在本批改 request.ts，则「复合链路共用同一 trace」无法实现（响应侧 trace 由网关生成，前端不传就串不起来）。上下文生命周期挂在详情页可保证「查看→研判→定责→回电→关单」共用同一 trace，离开即清不污染其他页面。
- **边界声明**：`shared/request.ts` 的改动需在 tasks 中单列，并回归 `shared/__tests__/request.spec.ts` 既有 11 条用例防止回归。
- **备选**：全局长驻 trace → 否决（跨页面串联无意义且污染列表类请求）；模块内自建 axios 实例 → 否决（违反 SSOT §9.3 单实例铁律）。

### D6 类型与枚举以后端源码为唯一来源
`types.ts` 字段与后端 snake_case 逐字一致；`task_status`、`verdict`（四值无 normal）、`review_action`（六值）、定责三值等枚举在实现期从 `complaints.py`/`mode.py` 核实后写入，并用单测锁定集合；SSOT 与后端冲突时以后端为准并回填 SSOT。
- **理由**：spec 硬约束「前端禁止发明状态」；枚举漂移是旧演示页的主要缺陷来源。

### D7 演示入口 DEV 门控的实现位置
「演示灌数/事件」按钮在列表页以 `import.meta.env.DEV` 条件渲染；api 函数照常定义在 `api.ts`（契约测试覆盖），仅 UI 触发器被门控。
- **理由**：门控是展示层职责；api 层保持一致可测，生产构建中未被引用的触发器自然不存在。

### D8 列表状态过滤由后端实现（实施期补充，2026-10-23 用户批准）
批次 1 实施中发现后端列表端点（`complaints.py` 路由与 `complaint_service.list()`）均无 `task_status` 过滤参数，与 spec「复核工作台列表请求仅命中待复核状态集合」冲突。采用方案 A：后端补 `task_status: Optional[list[TaskStatus]] = Query(default=None)` 重复查询参数（FastAPI 原生），service 过滤先于分页计数；前端 `paramsSerializer.indexes:false` 匹配重复参数序列化。此为「禁止改后端」边界的唯一例外，已获用户显式批准并回填 SSOT v1.3。
- **理由**：前端过滤会使分页 `total` 语义失真（筛后 0 条页、计数错误），生产不可接受；后端过滤是唯一让 spec Scenario 字面成立的解。备选（纯前端过滤降级 / 搁置筛选）经评审否决。

### D9 Prompt 模板加载器防御式 fallback（实施期补充，2026-10-23 用户批准）
批次 1 验收发现既有测试 `test_trace_after_run_graph_has_agent_and_llm` 红，根因是 `common/prompt/loader.py` 的 `_resolve_templates_dir` 用仓库相对路径硬编码 `services/complaint-service/app/prompt/templates`，在容器内（源码挂 `/app/app`）解析成了不存在的 `/app/services/complaint-service/app/prompt/templates`，而 dev compose 的 `PROMPT_TEMPLATES_DIR` 环境变量虽已存在但 6h 前启动的容器未刷新（需 `--force-recreate` 才生效）。修复：① `docker compose up -d --force-recreate complaint-service` 让既有 env var 生效；② loader.py L119-145 加防御式 fallback（硬编码路径不存在时尝试 `/app/app/prompt/templates` 并打 warning 日志），避免 compose 漏配 env var 时 silent 失败。后端 32 条全绿（修复前 31 pass + 1 fail），无副作用。
- **理由**：compose up -d 不自动刷新已启动容器的 env var 是运维常识，但项目易遗忘；防御式 fallback 让即使 compose 配错也不会 6h 没人发现，warning 日志可观测。


## Risks / Trade-offs

- [judge 110s 内用户离开详情页，请求在途且轮询已清理] → 防连点状态存组件本地；重新进入时按 `GET /{id}` 的 `task_status` 恢复 judging 态与轮询，不依赖内存状态。
- [后端 `GET /mode` 缺失导致用户无法确认当前模式] → 页面只展示切换结果并在 UI 文案注明「当前模式查询待后端补端点」；不臆造。
- [task_status / review_action 枚举与后端漂移] → 实现期逐字段对照 complaints.py，单测断言枚举集合；发现漂移以后端为准并回填 SSOT。
- [stats 返回字段未在 SSOT 逐字定义] → 实现期对照 `stats.py:18` 响应模型渲染字段，不自行聚合计算。
- [薄包装组件导致 keepAlive 缓存两份列表状态] → 这是预期行为（工作台与全量列表筛选不同）；`onActivated` 刷新保证数据新鲜。

## Migration Plan

纯前端新增，无数据迁移。步骤：① api.ts + types.ts + 契约测试 → ② PageTable 下沉 + 列表页/复核工作台 → ③ 详情页与操作组 → ④ 统计/模式切换 → ⑤ 单测与四道门禁 → ⑥ 真实浏览器冒烟。回滚 = 还原 `modules/complaint/` 为占位 routes、移除 PageTable（若暂无其他消费者），`app/router/index.ts` 装配行不变。

## Open Questions

- `GET /status` 返回的节点进度结构（节点名/状态字段）与 `GET /agent-logs` 的条目字段——用户在旧页调研中标注的两个待查项；实现期细读 `complaints.py:287`/`:332` 响应模型核实，不影响 spec 与任务拆分。

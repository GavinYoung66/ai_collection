# frontend/complaint-console Specification

## Purpose

智能投诉管理台前端模块：为 admin/supervisor/agent 提供投诉单列表、手工建单、详情（进度轮询/证据/agent 日志/trace/结论卡）、定责/回电/复核/关单操作组、复核工作台、统计展示与运行模式切换的正式生产界面，接口契约以 SSOT《frontend/前端架构.md》v1.2 §5.0 B 表 16 端点为准。

## Requirements

### Requirement: 投诉模块路由必须按 SSOT §11.1 装配并带角色约束
模块 MUST 提供三条路由：`/complaints`（ComplaintList，keepAlive）、`/complaints/review`（ComplaintReview，keepAlive，声明 MUST 在 `:id` 之前）、`/complaints/:id`（ComplaintDetail，不缓存）；三条 `meta.roles` MUST 为 `["admin","supervisor","agent"]` 且 `meta.title/icon` 齐全；视图组件 MUST 以 `() => import()` 懒加载（SSOT §15）；F0 占位路由 MUST 被替换，装配沿用编译期静态 import 契约。

#### Scenario: 静态段路由优先于参数段
- **WHEN** 读取模块 routes 数组并访问 `/complaints/review`
- **THEN** `review` 声明在 `:id` 之前，渲染复核工作台而非详情页

#### Scenario: 视图组件懒加载
- **WHEN** 首屏仅访问登录页
- **THEN** complaint 视图 chunk 未被加载，进入 `/complaints` 时才按需加载对应 chunk

#### Scenario: 越权角色被守卫拦截
- **WHEN** `ai_trainer` 登录并直接访问 `/complaints`
- **THEN** 跳转 `/403`，页面组件不渲染、不发起数据请求

#### Scenario: 列表页 keepAlive 生效
- **WHEN** 从列表页进入详情再返回
- **THEN** 列表页经 keepAlive 缓存（组件 `defineOptions.name` 与路由 name 逐字一致），`onActivated` 触发刷新而 `onMounted` 不重复拉取

### Requirement: API 层必须逐条对齐 §5.0 B 表且只走全前缀
`api.ts` MUST 覆盖 B 表全部 16 端点（method+path 逐条一致），一律 `/api/v1/complaints/*` 全前缀；MUST NOT 调用 `/api/v1/stats`、`/api/v1/mode` 孤儿路由；MUST 只 import `shared/request`；字段名与后端 snake_case 逐字一致；可选字段 `null` MUST NOT 当作 `undefined` 或空串处理，时间按 ISO 8601 字符串透传不隐式时区改写，主键 UUID 一律按 string；超时 MUST 只引用 `TIMEOUT` 常量表，MUST NOT 出现超时数值字面量。

#### Scenario: 16 端点契约逐条通过
- **WHEN** 执行 api.ts 契约单测
- **THEN** 列表、建单、demo/seed、events、详情、judge、status、verdict、agent-logs、trace、disposition/verdict、disposition/callback、review、close、stats、mode 共 16 条的 method+path 与 B 表逐条一致

#### Scenario: 孤儿路由零命中
- **WHEN** 在 `modules/complaint/` 内检索 `/api/v1/stats`、`/api/v1/mode`（非 complaints 前缀写法）
- **THEN** 命中数为零

#### Scenario: judge 使用独立超时常量
- **WHEN** 调用研判触发
- **THEN** 请求配置 `timeout === TIMEOUT.judge`（110 000ms），模块内无超时数值字面量

### Requirement: 列表页必须提供分页、状态筛选与行内操作
列表页 MUST 分页请求（`page` 从 1 起、`page_size` 默认 20、上限 100，组件默认值与后端一致；响应结构为 `{items,total,page,page_size}`，列表数据只取 `items`）；MUST 显式处理 loading/empty/error 三态（PageTable 内置）；MUST 提供状态筛选（枚举值以后端源码为准）；每行 MUST 提供「研判」「详情」行内操作；MUST NOT 实现旧演示页的 pending 自动研判。

#### Scenario: 默认分页参数
- **WHEN** 首次进入列表页
- **THEN** 请求参数为 `page=1&page_size=20`

#### Scenario: page_size 上限钳制
- **WHEN** 用户设置 page_size 大于 100
- **THEN** 实际请求被钳制为 100

#### Scenario: 行内研判不连带触发
- **WHEN** 点击某行「研判」
- **THEN** 仅对该行调用 judge 并进入 loading 防连点，其他行不被自动触发

### Requirement: 演示入口必须仅开发环境可见
「演示灌数」（POST /demo/seed）与「事件列表」（GET /events）入口 MUST 仅在 `import.meta.env.DEV` 为真时渲染；生产构建 MUST 不出现入口与触发器。

#### Scenario: 开发环境可见可用
- **WHEN** 开发模式进入列表页并点击演示灌数
- **THEN** 成功后可区分场景的 3 条新工单出现在列表

#### Scenario: 生产构建隐藏
- **WHEN** 生产构建渲染列表页
- **THEN** 页面无演示灌数与事件列表入口

### Requirement: 手工建单必须走统一信封并防重复提交
建单表单 MUST 调 `POST /api/v1/complaints`；成功 MUST 提示并刷新列表；失败 MUST 走 error-map 白名单文案并附 trace 后 8 位；提交中 MUST 禁用提交按钮。

#### Scenario: 建单成功刷新列表
- **WHEN** 提交合法表单且后端返回 code 0
- **THEN** 提示成功，列表刷新并出现新工单

#### Scenario: 提交中防重复
- **WHEN** 建单请求未返回时再次点击提交
- **THEN** 按钮处于禁用态，不产生第二次请求

### Requirement: 复核工作台必须是独立的左右分栏待办页
`/complaints/review` MUST 是独立页面（不复用 `ComplaintList`）：左栏为待复核队列（请求固定携带待复核状态集合，筛选控件 MUST NOT 出现），右栏为选中工单的复核面板；待复核状态集合 MUST 以后端 `app/models/state.py` 为准（至少含 `pending_review`，若存在 `pending_senior_review` 等复核态 MUST 一并纳入）。右栏 MUST 在原地完成定责确认/人工复核/回电登记，MUST NOT 用「跳转到详情页」代替；MUST NOT 提供触发研判、建单等接入侧操作。需要 Trace 时间线/Agent 日志时 MAY 提供「打开完整详情」跳转。
（2026-10-23 修订：初版为「复用列表页 + 锁定筛选」，实测与投诉列表视觉无从区分，经用户批准改为分栏独立页。）

#### Scenario: 队列请求固定命中待复核集合
- **WHEN** 进入 `/complaints/review`
- **THEN** 队列请求仅命中待复核状态集合内的工单，集合取值与 `state.py` 逐字一致，页面无状态筛选控件

#### Scenario: 原地复核不跳页
- **WHEN** 在左栏点选一条工单
- **THEN** 右栏原地展示投诉原文/AI 研判结论/AI 取证链/应答草稿/定责确认/人工复核，URL 不变；提交后重拉详情并刷新队列

#### Scenario: 按状态收敛操作
- **WHEN** 选中工单的 task_status 为 `pending_callback`
- **THEN** 右栏只出现回电登记，不出现定责确认与人工复核

### Requirement: 详情页必须呈现状态步骤条、定责对照与结论卡
详情页 MUST 展示：状态步骤条（task_status 枚举以后端源码为准，前端 MUST NOT 发明状态）、AI 初选与人审定责对照区、结论卡（`GET /verdict` 返回的 `verdict/confidence/conflicts` 等字段）；展示字段与后端 snake_case 逐字一致。

#### Scenario: 研判中状态高亮
- **WHEN** 打开 task_status=judging 的工单详情
- **THEN** 步骤条高亮「研判中」节点

#### Scenario: 改判对照展示
- **WHEN** 人审结论与 AI 初选不一致
- **THEN** 对照区同时展示 AI 初选值、人审终值与改判原因

### Requirement: 轮询必须满足 §14.1 契约且只由 task_status 驱动
点击研判 MUST `await POST /{id}/judge`（`TIMEOUT.judge`）且全程防连点；judge 请求成败仅影响提示，轮询生命周期 MUST 只由 `task_status` 驱动。轮询 MUST 满足：只打 `GET /{id}/status` 禁打详情端点；首轮 3s，连续 20s 状态无变化退避到 5s、上限 10s；`task_status ∈ {resolved, closed, archived}`（枚举对齐 `app/models/state.py`）立即停止并补拉一次 `/verdict` 与 `/agent-logs`；等待人工态（`pending_review`/`pending_senior_review`/`pending_callback`）由页面决定低频继续或停止并提示；`document.hidden` 为真暂停计时，`visibilitychange` 恢复后立即补一次；连续 3 次请求失败熔断停止并展示错误 + trace 后 8 位；`onUnmounted` MUST 清理定时器。

#### Scenario: 退避与终态停止
- **WHEN** status 连续 20s 返回 judging 无变化，之后变为 resolved
- **THEN** 间隔从 3s 退避到 5s（不超过 10s），resolved 后停止轮询并补拉 verdict 与 agent-logs

#### Scenario: 页面隐藏暂停、恢复补拉
- **WHEN** 轮询期间页面 `document.hidden` 变真再恢复可见
- **THEN** 隐藏期间不计时发请求，恢复后立即补发一次 status

#### Scenario: 失败熔断
- **WHEN** status 轮询连续 3 次请求失败
- **THEN** 停止轮询并展示错误 + trace 后 8 位，不无限重试

#### Scenario: judge 失败不干扰轮询
- **WHEN** judge 请求失败但详情 `task_status` 仍为 judging
- **THEN** 仅展示 judge 失败提示，status 轮询按契约继续直至终态或熔断

#### Scenario: 离开页面清理轮询
- **WHEN** judging 期间离开详情页
- **THEN** 轮询定时器被清理，不再发出 status 请求

### Requirement: 定责确认必须三选一且改判必填原因
定责表单 MUST 仅接受后端三值（`agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience`），MUST NOT 提交 `pending`；与 AI 初选不一致时 MUST 要求非空改判原因方可提交；成功 MUST 刷新详情状态。

#### Scenario: 与 AI 一致可直接提交
- **WHEN** 人审结论与 AI 初选相同且原因为空
- **THEN** 校验通过并提交成功

#### Scenario: 改判原因为空被拦截
- **WHEN** 人审结论与 AI 初选不同且改判原因为空
- **THEN** 表单校验拦截，不发出请求

### Requirement: 人工复核必须按后端 review_action 枚举提交
复核操作 MUST 调 `PUT /{id}/review`，`review_action` 可选值 MUST 与后端枚举逐字一致（以后端源码为准），MUST NOT 自造动作值；提交中 MUST 防连点，成功 MUST 刷新详情。

#### Scenario: 复核动作对齐后端
- **WHEN** 检查前端复核动作选项
- **THEN** 与后端 review_action 六值枚举逐字一致

#### Scenario: 复核提交成功
- **WHEN** 选择复核动作并确认
- **THEN** 调用 PUT review，成功后详情状态刷新

### Requirement: 回电登记与关单必须呈现后端前置校验结果
回电登记 MUST 调 `PUT /{id}/disposition/callback`；关单 MUST 调 `PUT /{id}/close`；后端前置校验失败（如未定责、未回电）MUST 原地展示后端 message 并附 trace 后 8 位；前端 MUST NOT 自造状态机校验替代后端判定。

#### Scenario: 前置不满足关单被拦
- **WHEN** 未完成回电登记即点击关单
- **THEN** 展示后端返回的缺失步骤文案 + trace 后 8 位，不发生跳转

#### Scenario: 重复回电按幂等成功呈现
- **WHEN** 对已登记回电的工单再次提交回电登记
- **THEN** 按后端幂等成功语义呈现，不报失败

### Requirement: 详情 Tab 必须覆盖证据、trace、agent 日志与草稿并按需加载
详情页 MUST 提供 Tab：证据包（取详情响应 `ordered_evidence` + `evidence_refs`，支持 RAG 源 ID 回溯）、全链路 trace（GET /trace 事件时间线）、agent 日志（GET /agent-logs）、草稿；Tab 数据 MUST 在首次切换到时加载，字段与后端一致。

#### Scenario: trace 事件时间线
- **WHEN** 切换到 trace Tab
- **THEN** 按时间序渲染事件（类别/名称/耗时或起止/状态摘要）

#### Scenario: 未切换不请求
- **WHEN** 从未切换到某 Tab
- **THEN** 对应该 Tab 的请求未发出

### Requirement: 统计展示必须基于 stats 端点且不引入图表库
统计视图 MUST 调 `GET /api/v1/complaints/stats`，以基础组件（卡片/表格）呈现后端返回字段；MUST NOT 引入 ECharts 或等价图表依赖。

#### Scenario: 统计数据渲染且无图表依赖
- **WHEN** 进入统计视图并检查依赖清单
- **THEN** 展示后端统计字段，package.json 与构建产物中无 echarts

### Requirement: 运行模式切换为灰度运维能力，业务页面不得暴露入口
模式切换是运营/技术负责人的灰度运维能力（双签 + 灰度比例 + 失败回滚 `A5014`，见 `app/service/mode_service.py`），MUST NOT 出现在投诉业务页面（列表/工作台/详情）的任何入口。前端 MUST 保留 `api.ts` 的 `switchComplaintMode` 与 `ModeSwitchRequest/Response` 类型以便将来运维后台复用；因后端无 `GET /mode`，任何展示都 MUST NOT 臆造「当前模式」。
（2026-10-23 修订：初版要求列表页提供双签弹窗，实测该入口对业务用户无意义且易被误解为「AI Agent 模式」，经用户批准下线。）

#### Scenario: 业务页面无模式切换入口
- **WHEN** 打开投诉列表、复核工作台或详情页
- **THEN** 页面中不存在「运行模式切换」按钮，也不存在「目标模式/切换理由」弹窗

#### Scenario: API 能力保留
- **WHEN** 检查 `modules/complaint/api.ts`
- **THEN** `switchComplaintMode` 仍导出且契约单测覆盖 `PUT /api/v1/complaints/mode`

### Requirement: 错误处理必须统一走信封与白名单并落实模块侧动作
模块所有错误提示 MUST 经 `request.ts` 信封解析与 error-map 白名单分派，提示附 trace 后 8 位（缺失显示「无」）；MUST NOT 在模块内自造错误码文案映射；MUST NOT 以 HTTP 状态判定业务成败。F1 相关码的模块侧动作 MUST 为：`A5001` 按 code 跳 404 空态 + 返回列表入口（不依赖 HTTP 404）；`A5002` 提示「状态已变更」并自动刷新详情；`A5014` ElMessageBox 强警示并注明当前模式查询待后端补端点；`B0005` 幂等提示不展示为错误；`B0010` 强提示且不提供重试；`A5015/A5016/A5017` 正向提示并引导至复核队列。

#### Scenario: 未映射错误码兜底
- **WHEN** 后端返回白名单外 code
- **THEN** 展示后端 message 原文 + trace 后 8 位，不抛异常

#### Scenario: 工单不存在按 code 进空态
- **WHEN** 深链打开详情，`GET /{id}` 返回 HTTP 200 + `code: "A5001"`
- **THEN** 详情区渲染 404 空态并提供返回列表入口，不当作网络异常

#### Scenario: 状态冲突自动刷新
- **WHEN** 操作返回 `A5002`
- **THEN** 提示「状态已变更」并自动重新拉取详情

#### Scenario: 转人审类错误码正向提示
- **WHEN** 后端返回 A5015/A5016/A5017
- **THEN** 以正向提示样式呈现并引导至复核队列，不展示为失败

### Requirement: X-Trace-Id 必须在详情页复合链路落地
`shared/request.ts` 的请求拦截器 MUST 从模块级 trace 上下文读取当前 trace_id 并注入 `X-Trace-Id` 请求头（F0 仅预留 `setTraceContext/getTraceContext`，**未实现注入**，本批 MUST 补上；该文件其余逻辑——Bearer、信封解析、401/429、超时——MUST NOT 改动）；进入详情页时 store/composable MUST 写入 trace 上下文以串联「查看→研判→定责→回电→关单」复合链路，离开详情页 MUST 清空；业务代码 MUST NOT 手写该请求头。

#### Scenario: 详情页操作共用同一 trace
- **WHEN** 进入某工单详情后依次执行研判与定责
- **THEN** 两次请求头携带同一 `X-Trace-Id`，可在后端日志串联该链路

#### Scenario: 离开详情清空上下文
- **WHEN** 从详情页返回列表后再发起列表请求
- **THEN** trace 上下文已清空，列表请求不携带详情页的 trace_id

# frontend/qc-console Specification

## Purpose

合规质检管理台前端模块：为 admin/supervisor/qc_reviewer 提供质检任务台（待检队列/灌数/开检/批量）、质检报告（列表/统计/详情：命中表/条款/trace）、人工复核队列、规则只读+热应用、告警流的正式生产界面，接口契约以 SSOT《frontend/前端架构.md》§5.0 C 表 15 端点为准，平移并取代 `services/qc-service/web/` 旧演示页。

## Requirements

### Requirement: 质检模块路由必须按 SSOT §11.1 装配
模块 MUST 提供六条路由：`/qc/tasks`（QcTaskBoard，keepAlive）、`/qc/records`（QcRecordList，keepAlive，声明 MUST 在 `:sessionId` 之前）、`/qc/records/:sessionId`（QcRecordDetail，不缓存）、`/qc/reviews`（QcReview，keepAlive）、`/qc/rules`（QcRules，keepAlive）、`/qc/alerts`（QcAlerts，keepAlive）；`meta.title/icon` MUST 齐全；视图组件 MUST 以 `() => import()` 懒加载；F0 占位路由 MUST 被替换，`app/router/index.ts` 装配行 MUST NOT 改动。

#### Scenario: 静态段路由优先于参数段
- **WHEN** 读取模块 routes 数组并访问 `/qc/records`
- **THEN** 记录列表声明在 `:sessionId` 之前，渲染列表页而非详情页

#### Scenario: 视图组件懒加载
- **WHEN** 首屏仅访问登录页
- **THEN** qc 视图 chunk 未被加载，进入 `/qc/tasks` 时才按需加载对应 chunk

#### Scenario: 列表页 keepAlive 生效
- **WHEN** 从记录列表进入详情再返回
- **THEN** 列表页经 keepAlive 缓存（组件 `defineOptions.name` 与路由 name 逐字一致），`onActivated` 触发刷新而 `onMounted` 不重复拉取

### Requirement: 路由角色必须与后端 allowlist 收窄对齐
`meta.roles` MUST 与后端 `require_roles` 词表对齐：`/qc/rules` 为 `["admin"]`，`/qc/alerts` 为 `["admin","supervisor"]`，`/qc/reviews` 为 `["supervisor","qc_reviewer"]`，其余三条为 `["admin","supervisor","qc_reviewer"]`；侧边栏菜单与路由守卫 MUST 共用同一份 `meta.roles`，不得出现菜单隐藏但 URL 可进的不一致。

#### Scenario: 收窄角色被守卫拦截
- **WHEN** `qc_reviewer` 登录并直接访问 `/qc/rules` 或 `/qc/alerts`，或 `admin` 直接访问 `/qc/reviews`
- **THEN** 跳转 `/403`，页面组件不渲染、不发起数据请求，且侧边栏不显示对应菜单项

### Requirement: API 层必须逐条对齐 §5.0 C 表且只走全前缀
`api.ts` MUST 覆盖 C 表全部 15 端点（method+path 逐条一致），一律 `/api/v1/qc/*` 全前缀；MUST 只 import `shared/request`；字段名与后端 snake_case 逐字一致；可选字段 `null` MUST NOT 当作 `undefined` 或空串处理，时间按 ISO 8601 字符串透传，UUID 按 string；超时 MUST 只引用 `TIMEOUT` 常量表，replay/batch MUST 使用 `TIMEOUT.qcReplay`（110 000ms），其余使用 `TIMEOUT.normal`，模块内 MUST NOT 出现超时数值字面量。

#### Scenario: 15 端点契约逐条通过
- **WHEN** 执行 api.ts 契约单测
- **THEN** demo/seed、demo/pending、demo/{session_id}/trace、records 列表/stats/详情、replay/raw、replay/{session_id}、check/batch、reviews/pending、reviews/{qc_record_id}、alerts 列表/ack、rules/active、rules/apply 共 15 条的 method+path 与 C 表逐条一致

#### Scenario: replay/raw 携带幂等头
- **WHEN** 调用原始会话回放
- **THEN** 请求头携带 `X-Idempotency-Key`（UUID），缺失时后端 422 属契约违背

#### Scenario: replay 与批量使用长超时常量
- **WHEN** 调用 replay/{session_id} 或 check/batch
- **THEN** 请求配置 `timeout === TIMEOUT.qcReplay`（110 000ms），其余端点为 `TIMEOUT.normal`

### Requirement: 任务台必须提供待检队列、开检与批量质检
任务台 MUST 展示待检会话队列（GET demo/pending）；点击待检行 MUST 调 replay/{session_id} 开检，全程展示加载态并禁止对该行重复触发；点击已出分行 MUST 仅拉取 records/{session_id} 详情，MUST NOT 自动重跑；MUST 提供批量勾选与批量质检（check/batch），勾选数量 MUST 有上限（终值见 tasks，超限禁用并提示大批量走后端异步）。

#### Scenario: 开检防连点
- **WHEN** 用户对同一待检行连续点击开检
- **THEN** 仅首发请求生效，行内 loading 期间后续点击被忽略

#### Scenario: 已出分行不重跑
- **WHEN** 点击状态为已出分的行
- **THEN** 仅发起 records/{session_id} 查询，不发起 replay 请求

#### Scenario: 批量超限禁用
- **WHEN** 勾选数量超过上限
- **THEN** 批量质检按钮禁用并提示大批量走后端异步（P1）

### Requirement: 演示入口必须仅开发环境可见
「灌入演示数据」（POST demo/seed）与原始会话回放（POST replay/raw）入口 MUST 仅在 `import.meta.env.DEV` 为真时渲染；生产构建 MUST 不出现入口与触发器。

#### Scenario: 开发环境可见可用
- **WHEN** 开发模式进入任务台并点击灌数
- **THEN** 成功后待检队列出现新会话且重复灌数 session_id 不冲突

#### Scenario: 生产构建隐藏
- **WHEN** 生产构建渲染任务台
- **THEN** 页面无灌数与 replay/raw 入口

### Requirement: 报告列表必须以扣分列呈现并提供统计卡
报告列表 MUST 分页请求（默认值与上限以 records.py 实际签名为准）；台账 MUST 以「扣分」列呈现，MUST NOT 展示 grade 列；统计卡 MUST 基于 GET records/stats 返回字段呈现，前端不自行聚合；MUST NOT 引入 ECharts 等图表库。

#### Scenario: 台账扣分列
- **WHEN** 渲染报告列表
- **THEN** 列表展示 deduction_total 对应扣分列，无 grade 列与合规分列

#### Scenario: 统计卡透传后端字段
- **WHEN** stats 接口返回统计字段
- **THEN** 卡片渲染字段与响应逐字一致，无前端二次聚合

### Requirement: 详情页必须呈现扣分、命中、条款、skip 与 trace
详情 MUST 展示 deduction_total、compliance_score、risk、qc_status、qc_result，MUST NOT 展示 grade；命中表 MUST 含来源（ac|rule|llm）、rule_id 或词、证据、本条扣分 points（high 20 / medium 8 / low 2）、客户侧 R13 标注不计入、policy_clause 与 policy_text；kb_snippet 若存在 MUST 独立展示，MUST NOT 与 policy_text 混用；skipped_rules MUST 展示且文案表明本通未审/未适用，MUST NOT 称为已合规；MUST 提供会话原文对照并高亮命中证据；trace 时间线 MUST 取 demo/{session_id}/trace，仅当存在 langsmith_url 时显示外链。

#### Scenario: 命中表条款与片段分离
- **WHEN** 详情数据同时含 policy_text 与 kb_snippet
- **THEN** 两者分区块呈现，不合并、不互相替换

#### Scenario: skip 文案不误导
- **WHEN** 记录含 skipped_rules
- **THEN** 文案为「本通未审/未适用」，不出现「已合规」表述

#### Scenario: 客户侧 R13 不计入
- **WHEN** 命中项含 R13
- **THEN** 该行标注「客户侧不计入」，扣分合计不含其 points

#### Scenario: trace 外链条件渲染
- **WHEN** trace 响应无 langsmith_url
- **THEN** 时间线正常渲染但不出现 LangSmith 外链

### Requirement: 人工复核必须按后端枚举与扣分状态呈现
零扣分（deduction_total=0）时 MUST NOT 展示人审提交按钮，状态 DONE 即为完成；有扣分时 MUST 提供「扣分正确」（映射 CONFIRM，不改扣分）与「误报/改扣分」（映射 CORRECT，MUST 手填 revised_deduction，>=0 整数）；前端 MUST NOT 暴露 DISMISS；REVIEWED 后按钮 MUST 禁用；复核操作仅当前用户角色 ∈ {qc_reviewer, supervisor} 时可用，否则置灰并说明原因（含详情页内嵌人审按钮组）。

#### Scenario: 零扣分无提交入口
- **WHEN** 打开 deduction_total=0 的记录
- **THEN** 页面不出现 CONFIRM/CORRECT 提交按钮

#### Scenario: 改扣分必填校验
- **WHEN** 选择「误报/改扣分」但未填 revised_deduction
- **THEN** 提交被拦截并提示必填

#### Scenario: REVIEWED 禁用
- **WHEN** 记录 qc_status 为 REVIEWED
- **THEN** 全部人审按钮禁用

#### Scenario: admin 人审置灰
- **WHEN** admin 打开有扣分记录的详情页
- **THEN** 人审按钮组置灰并提示「仅 qc_reviewer/supervisor 可复核」，不发起请求

### Requirement: 规则管理必须为只读展示加热应用
规则页 MUST 展示生效规则列表（GET rules/active，R01~R14）；MUST NOT 提供规则编辑/新增/删除；规则热应用（POST rules/apply）MUST 二次确认后发起。

#### Scenario: 只读列表
- **WHEN** 进入规则页
- **THEN** 展示生效规则，页面无任何编辑入口

#### Scenario: 热应用二次确认
- **WHEN** 点击规则热应用
- **THEN** 弹出二次确认，确认后才发起 POST rules/apply

### Requirement: 告警流必须提供列表与确认
告警页 MUST 展示告警列表（GET alerts）；MUST 提供告警确认（POST alerts/{alert_id}/ack），确认中 MUST 防连点，成功后更新该条状态。

#### Scenario: 告警确认
- **WHEN** 对未确认告警点击确认
- **THEN** 调 ack 接口，提交中按钮禁用，成功后该条标记已确认

### Requirement: 错误处理必须统一走信封与白名单
模块 MUST 一律按信封 code 判定成败，HTTP 状态仅用于网络/网关诊断（qc 按 HTTP_STATUS_BY_CODE 分发：A2002→422、A2005/A2007→404、A2006→409、多数→200）；MUST 落实 §15.1 白名单模块侧动作：B0002 清态跳登录、B0003 403 提示不重试、B0004 详情空态+返回列表、B0005 幂等提示、B0006 按 Retry-After 退避、B0007/B0008/B0009 兜底+重试按钮、A2002 表单行内标红（qc 的 422 不叠 B0001）、A2003 质检按钮置灰+悬浮说明原因不发起请求；未列入的码 MUST 走默认分支 message 直出 + trace 后 8 位；MUST NOT 新增错误码映射。

#### Scenario: A2003 按钮置灰
- **WHEN** 开检收到 code=A2003
- **THEN** 该行质检按钮置灰并悬浮说明原因，不重复发起请求

#### Scenario: 网关 504 可解释提示
- **WHEN** replay 被网关 504（B0009）掐断
- **THEN** 提示兜底文案 + trace_id 后 8 位并提供重试按钮，不静默失败

#### Scenario: 未列入码默认分支
- **WHEN** 收到白名单外的 A20xx 码
- **THEN** message 直出并附 trace 后 8 位，无新增映射

### Requirement: X-Trace-Id 必须在详情页复合链路落地
详情页 MUST 在挂载时写入 trace 上下文、卸载时清空（复用 shared/request.ts 已落地的拦截器注入逻辑，注入代码零改动）；业务代码 MUST NOT 手写 X-Trace-Id 头；详情页「查看→开检→复核」连续操作 MUST 携带同一 X-Trace-Id。

#### Scenario: 详情页操作串联
- **WHEN** 详情页连续发起详情查询与复核提交
- **THEN** 两次请求头携带同一 X-Trace-Id，离开详情后列表请求不携带

# Spec Delta

## Purpose

frontend/usage-console 是 LLM 用量观测量前端能力（F3）：向 admin/supervisor 提供用量指标卡、时间范围切换、模型与服务分布、调用量×失败率双轴趋势、调用明细分页与失败筛选、trace_id 检索与韧性计数展示；接口契约为 DRAFT（后端端点与埋点待实现），本批以开发态 mock 验证行为。

## ADDED Requirements

### Requirement: 用量模块必须按 SSOT §11.1 装配唯一路由

模块 MUST 以 BizModule 自包含结构替换 F0 占位，仅装配 `/usage` 一条路由（name=`UsageDashboard`）；`meta.roles` MUST 为 `['admin','supervisor']`，keepAlive MUST 开启；视图 MUST 懒加载且 `defineOptions` name 与路由 name 逐字一致；`app/router/index.ts` 装配行 MUST 零改动；MUST NOT 新增子路由。

#### Scenario: 占位路由被替换且角色矩阵正确

- **WHEN** 模块实现完成并对照 §11.1:717 核对路由表
- **THEN** `/usage` / `UsageDashboard` / `['admin','supervisor']` / keepAlive=true 四项逐字一致，route-matrix 断言通过；agent/qc_reviewer/ai_trainer 直接访问被拦截至 `/403`

#### Scenario: 可插拔反向验证

- **WHEN** 临时删除 `modules/usage` 与 `app/router/index.ts` 装配行
- **THEN** `vue-tsc --noEmit && vite build` 仍通过；恢复后构建与单测无回归

### Requirement: API 层必须按 DRAFT 契约调用且限于 2 个端点

api 层 MUST 且仅 MUST 调用 `GET /api/v1/llm-usage/summary` 与 `GET /api/v1/llm-usage/logs`（根绝对路径全前缀），全部使用 `TIMEOUT.normal`；MUST NOT 出现超时数值字面量。契约字段 MUST 对齐 `common/db/models/llm_log.py` 既有列名。`status` 取值 MUST 以后端实际写入为准：`ok`（`llm_reviewer.py:466`）、`qc_review_exemption`（`llm_reviewer.py:390/407/425/436/449/459`）、`skipped`（`llm_reviewer.py:397`）；`llm_log.py:25` 注释的四值仅为计划值，MUST NOT 当作唯一枚举来源。前端 MUST 原样透出 status，MUST NOT 做取值映射。契约 MUST 以 DRAFT 身份标注，后端端点尚未实现（SSOT:251-252）。

#### Scenario: 端点与超时对照

- **WHEN** 验收时逐条核对 api 调用
- **THEN** 恰 2 条 method+path 命中，无表外调用；两处均使用 `TIMEOUT.normal`；模块内无 12000 等超时字面量

#### Scenario: 分页参数双向钳制

- **WHEN** 明细请求携带 page 或 page_size
- **THEN** page 低于 1 被钳制为 1，page_size 低于 1 钳制为 1、高于 100 钳制为 100，不依赖后端报错

### Requirement: 用量看板必须展示四项核心指标并可切换时间范围

看板 MUST 展示调用量、token 总量、平均时延、失败率四项指标卡，并提供近 1 天 / 7 天 / 30 天 / 自定义时间范围切换；切换 MUST 触发重新取数。看板空态 MUST 以 `totals.calls === 0` 判定并展示引导文案，不得渲染空白图表。

#### Scenario: 时间范围切换触发重取

- **WHEN** 用户将时间范围从近 7 天切到近 30 天
- **THEN** 请求携带新的 start/end 参数；四项指标卡按新响应刷新

#### Scenario: 无数据空态

- **WHEN** summary 返回 `totals.calls === 0`
- **THEN** 展示「所选时间范围内暂无调用记录」空态，不展示误导性的零值图表

### Requirement: 分布与趋势图必须支持数据缺失时整块隐藏

看板 MUST 渲染模型分布、调用量与失败率双轴趋势；服务分布 MUST 仅在 `by_service` 存在且非空时渲染，缺失时整块隐藏且不占位。图表 MUST 在 keepAlive 返回时重绘并跟随容器尺寸变化；组件卸载或失活时 MUST 移除尺寸监听。

#### Scenario: 服务分布数据缺失

- **WHEN** summary 响应不含 `by_service` 字段
- **THEN** 服务分布区块不渲染，页面无空图表、无 JS 报错，其余区块正常展示

#### Scenario: 缓存返回后尺寸正确

- **WHEN** 用户切走再切回 `/usage`（keepAlive 命中）并改变窗口宽度
- **THEN** 图表按当前容器宽度重绘，不出现画布被裁切或模糊

### Requirement: 调用明细必须支持分页、状态筛选与失败记录展开

明细 MUST 以分页列表展示 model、status、latency、token、create_time；page_size MUST 默认 20 且上限 100；MUST 提供状态筛选（含「失败」快捷项）。筛选可选值 MUST 由契约确认结果固定，未确认前按 `llm_log.py:25` 四值提供草案选项；列表 MUST 原样透出后端实际 status（含 `ok` / `qc_review_exemption` / `skipped`），MUST NOT 前端改写或归并。`error_msg` MUST 可展开查看。明细空态 MUST 以 `total === 0` 判定。

#### Scenario: 失败筛选

- **WHEN** 用户点击「失败」快捷筛选
- **THEN** 请求携带 status=failed（或后端确认的失败口径），列表仅展示失败记录，total 同步更新

#### Scenario: 实际写入值不被前端改写

- **WHEN** logs 返回 `status=qc_review_exemption` 或 `status=skipped` 等非注释四值
- **THEN** 列表逐字展示后端原值，无取值映射、无「未知状态」占位；筛选项与契约确认口径一致

#### Scenario: 展开失败原因

- **WHEN** 用户展开一条含 error_msg 的记录
- **THEN** 展示该记录的 error_msg 原文与 trace_id（可复制），不得截断为不可读占位

### Requirement: 必须支持 trace_id 精确检索并与链路追踪联动展示

明细 MUST 提供 trace_id 输入检索（精确匹配），结果 MUST 以共享 TraceIdText 展示且可复制；检索为单次动作，MUST NOT 引入模块级 trace 上下文或手写 `X-Trace-Id` 头。

#### Scenario: 按 trace_id 检索命中

- **WHEN** 用户输入存在的 trace_id 并提交
- **THEN** 请求携带 trace_id 参数，列表仅显示该链路的调用记录，页面展示可复制的 trace_id

#### Scenario: 检索无结果

- **WHEN** 输入的 trace_id 无匹配记录
- **THEN** 展示「未找到该 trace_id 的调用记录」空态，不弹全局错误提示

### Requirement: 韧性状态必须由接口提供且缺失时隐藏

页面 MUST 展示限流、熔断、fallback 触发次数（取 `summary.resilience`）；字段缺失时 MUST 整块隐藏；前端 MUST NOT 自行计算或以 Prometheus 指标替代。

#### Scenario: 韧性字段存在

- **WHEN** summary 返回 `resilience: { rate_limited, breaker_open, fallback }`
- **THEN** 三项计数按返回原值展示，不做二次加工

#### Scenario: 韧性字段缺失

- **WHEN** summary 不含 resilience
- **THEN** 韧性区块整块不渲染，无占位空卡

### Requirement: 页面必须常驻数据完整性声明

页面 MUST 常驻展示横幅「用量数据当前仅 qc-service 上报；complaint/rag 埋点与 token/时延字段补齐前，分布与均值可能不完整」，文案 MUST NOT 暗示数据为全量或完整。

#### Scenario: 横幅不随数据变化消失

- **WHEN** 任意时间范围、任意筛选条件下进入 `/usage`
- **THEN** 横幅始终可见，且与是否存在数据无关

### Requirement: 错误与空态必须按 SSOT §15.1 白名单处理且零新增映射

错误处理 MUST 复用既有白名单分支，MUST NOT 新增错误码映射；看板空态与明细空态 MUST 分开判定；B0004 / B0006 / B0007 / B0008 / B0009 按既有分支处置；本页只读，无权限按钮，MUST NOT 出现写操作入口。

#### Scenario: 限流与网关错误

- **WHEN** summary 返回 B0006（含 Retry-After）或 B0009
- **THEN** 按既有分支提示并可重试，页面不崩溃、不重复自动轮询

#### Scenario: 零新增映射

- **WHEN** 验收时检查 error-map 与相关断言
- **THEN** 映射表条目数与基线一致，新增映射为 0，单测断言通过

### Requirement: 交付验收必须以 mock 驱动为准并显式声明真链路未验收

本批验收 MUST 以开发态 mock 驱动全部 Scenario 通过为准；验收报告 MUST 显式写明真链路未验收及三项原因（端点不存在、网关无 location、埋点缺口）。原 F3 退出标准「与 llm_call_logs 表 count 一致」MUST NOT 被表述为已达成。

#### Scenario: 验收报告口径

- **WHEN** 输出最终验收报告
- **THEN** 包含 mock 驱动 Scenario 通过率、四道门禁结果、「真链路未验收」声明，且不含任何「已实现后端」表述

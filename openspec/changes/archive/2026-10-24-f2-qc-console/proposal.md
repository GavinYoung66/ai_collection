# Proposal

## Why

F0 管理台底座已归档、F1 智能投诉已完成（19/19 tasks，master CI 全绿）；`/qc/tasks` 仍为 F0 占位页。旧演示页 `services/qc-service/web/`（774 行原生 JS）承载质检演示能力，但无鉴权、无工程化保障，与 SSOT《frontend/前端架构.md》§5.0 C 表 15 个已上线端点脱节。本批（F2 合规质检线）将质检功能完整平移进 Vue 3 管理台，使 admin/supervisor/qc_reviewer 获得与 §5.3 功能树、§11.1 路由表逐条对齐的正式生产界面。

## What Changes

- 新建 `frontend/src/modules/qc/` 完整业务模块（`index.ts` / `routes.ts` / `api.ts` / `types.ts` / `views/` / `components/` / `composables/` / `__tests__/`），替换 F0 占位路由，满足 BizModule 自包含契约；`app/router/index.ts` 装配行零改动（qcModule 已挂载）。
- 接入 §5.0 C 表全部 15 端点（一律 `/api/v1/qc/*` 全前缀）：demo/seed、demo/pending、demo/{session_id}/trace、records 列表/stats/详情、replay/raw、replay/{session_id}、check/batch、reviews/pending、reviews/{qc_record_id}、alerts 列表/ack、rules/active、rules/apply。
- 路由按 §11.1 落 6 条：`/qc/tasks`（QcTaskBoard）、`/qc/records`（QcRecordList，必须先于 `:sessionId` 声明）、`/qc/records/:sessionId`（QcRecordDetail，不缓存）、`/qc/reviews`（QcReview）、`/qc/rules`（QcRules）、`/qc/alerts`（QcAlerts）；`meta.title/icon` 齐全；列表页 keepAlive 且 `defineOptions` name 与路由 name 逐字一致，`onActivated` 刷新、`onMounted` 不重复拉取；视图一律 `() => import()` 懒加载；同步更新 `app/__tests__/route-matrix.spec.ts`。
- **角色收窄（本次 SSOT 修正，依据后端 `app/routers` 的 `require_roles` 词表，无 admin 通配）**：
  - `/qc/rules` → `['admin']`（rules/active=admin/ai_trainer、apply=admin；ai_trainer 按 §3.2 不归 QC 模块）
  - `/qc/alerts` → `['admin','supervisor']`（alerts=supervisor/admin）
  - `/qc/reviews` → `['supervisor','qc_reviewer']`（reviews/pending 与提交复核均不含 admin）
  - 其余 3 条保持 `['admin','supervisor','qc_reviewer']`（records/demo/check 端点无角色校验，仅验 token）
  - 详情页内嵌人审按钮组：仅当前用户角色 ∈ {qc_reviewer, supervisor} 可用，否则置灰+说明（§5.0 注「按钮级按 allowlist」既定策略）
  - 同批回填 SSOT §11.1 与 §3.2（走 §13 闭环，版本号递增、变更表登记）。
- **超时放行（照 F1 judge 先例）**：`deploy/gateway/server_common.conf.template` 新增两个正则 location——`^/api/v1/qc/replay/` 与 `^/api/v1/qc/check/batch$`，`proxy_read_timeout 120s`（正则优先于前缀匹配，照 judge location 模板复制）；前端 `shared/request.ts` 唯一改动为新增 `TIMEOUT.qcReplay = 110_000`（既有 normal/judge 与拦截器逻辑一律不动），replay/batch 使用该常量（前端值必须小于网关值，§9.3 铁律）；同批回填 SSOT §5.0 C 表与 §9.3 矩阵。
- 批量质检前端限流：任务台批量勾选设上限（默认 20，design 按「单条最坏耗时 × 上限 < 110s」核算后在 tasks 锁定终值；后端 `QC_BATCH_MAX=100` 且串行循环，超限禁用并提示大批量走后端异步，留 P1）；replay 异步化不在本批。
- 页面范围（对齐 §5.3 功能树）：
  - 任务台 `/qc/tasks`：待检队列（demo/pending）、灌数（demo/seed，仅 `import.meta.env.DEV` 可见）、行内开检（replay/{session_id}，全程加载态+防连点+禁同行重复触发）、批量质检（check/batch）；已出分行只拉 records/{session_id} 详情，不自动重跑；replay/raw 仅作 DEV 入口。
  - 报告 `/qc/records`：PageTable 分页列表 + stats 统计卡（不引 ECharts）；台账以「扣分」列呈现，不展示 grade 列；分页参数默认值与上限以 records.py 实际签名为准核实，若与 §5.0.1 不一致走 SSOT 闭环，不臆造。
  - 详情 `/qc/records/:sessionId`：展示 deduction_total/compliance_score/risk/qc_status/qc_result（不展示 grade）；命中表含来源 ac|rule|llm、rule_id 或词、证据、本条扣分 points（high 20 / medium 8 / low 2）、客户侧 R13 标注不计入、policy_clause 与 policy_text；kb_snippet 若存在独立展示，不得与 policy_text 混用；skipped_rules 文案必须为「本通未审/未适用」，不得称已合规；会话原文对照命中证据高亮；trace 时间线（demo/{session_id}/trace，有 langsmith_url 才显示外链）。
  - 人工复核 `/qc/reviews`：待复核队列（reviews/pending）；有扣分时「扣分正确」=CONFIRM（不改扣分）、「误报/改扣分」=CORRECT 且必须手填 revised_deduction（>=0 整数）；零扣分不展示提交按钮；任何形态不得暴露 DISMISS；REVIEWED 后按钮禁用。
  - 规则管理 `/qc/rules`：rules/active 只读列表（R01~R14，MVP 不做编辑）+ rules/apply 热应用（二次确认）。
  - 告警流 `/qc/alerts`：alerts 列表 + ack 确认。
- X-Trace-Id 复用 F1 已落地拦截器逻辑（注入代码零改动），新增 composable 照搬 complaint 模式：详情页挂载写入 trace 上下文、卸载清空，业务代码不手写该头。
- 错误处理严格按 §15.1 白名单（禁止新增映射）：B0002 清态跳登录、B0003 403 提示不重试、B0004 详情空态+返回列表、B0005 幂等提示、B0006 按 Retry-After 退避、B0007/B0008/B0009 兜底文案+trace_id+重试按钮、A2002 表单行内标红（qc 的 422 用 A2002 不叠 B0001）、A2003 质检按钮置灰+悬浮说明原因不发起请求；其余 A20xx 一律默认分支 message 直出+trace 后 8 位。qc 服务 HTTP 状态按 `HTTP_STATUS_BY_CODE` 分发（A2002→422、A2005/A2007→404、A2006→409、多数→200），前端一律按信封 code 判定成败，HTTP 状态仅用于网络/网关诊断。
- 契约核实（types.ts 逐字段来源行号记录）：15 端点请求/响应字段；replay/raw 必须带 `X-Idempotency-Key` 头（UUID），replay/{session_id} 与 batch 支持 `X-QC-Force-Rerun` 头/body.force；人审提交 action 三值枚举（CONFIRM/CORRECT/DISMISS，前端只用前两个）与 revised_deduction 约束；命中 points 档位与 R13 不计入规则。
- 废弃旧页行为：localStorage 缓存会话、页眉改 API_BASE、演示 JWT（role=QC）；列表以后端实时查询为准。
- 废弃旧页健康检查条（GET /health）：演示页调试工具，生产环境健康状态由运维监控承载，管理台不提供入口（8.2 对照时补登记）。
- 不引入 ECharts/SSE/WebSocket 等任何新运行时依赖；不改 qc-service 后端业务代码（唯一例外为网关模板加两个 location）；不改其他四模块；shared/ 既有逻辑零改动（唯一新增 `TIMEOUT.qcReplay` 常量；PageTable 与 X-Trace-Id 拦截器复用 F1 成果）。

## Capabilities

### New Capabilities

- `frontend/qc-console`: 合规质检管理台前端模块——质检任务台（待检队列/灌数/开检/批量）、质检报告（列表/统计/详情：命中表/条款/skip/会话对照/trace）、人工复核队列、规则只读+热应用、告警流，契约以 SSOT §5.0 C 表 15 端点为准。

### Modified Capabilities

（无。F0 `frontend/admin-foundation` 已显式预留模块演进点：占位路由按模块契约替换、PageTable 复用，无 REQUIREMENT 级变更。SSOT §11.1/§3.2/§5.0 C/§9.3 回填属文档闭环，非 spec 行为变更。）

## Impact

- **代码**：新增 `frontend/src/modules/qc/**`；`shared/request.ts` +1 常量（`TIMEOUT.qcReplay`，唯一 shared 改动）；`deploy/gateway/server_common.conf.template` +2 个正则 location；`app/__tests__/route-matrix.spec.ts` 同步；SSOT《frontend/前端架构.md》§11.1/§3.2/§5.0 C/§9.3 回填。
- **API**：消费 qc-service 既有 15 端点，无后端业务代码变更；网关配置变更需 reload 生效。
- **依赖**：不新增任何运行时依赖（不引 ECharts/SSE/WebSocket）。
- **验证**：四道 CI 门禁（eslint / vue-tsc / vitest / depcruise）须保持全绿；退出标准含 §5.0 C 表逐条对照、旧 app.js 功能对照（功能集合 ⊇）、反向可拔验证、按收窄后矩阵的三角色真实浏览器冒烟。

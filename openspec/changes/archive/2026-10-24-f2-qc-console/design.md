# Design

## Context

See proposal.md - Why。设计期已核实的现状约束：

- 前端底座（F0）与投诉模块（F1）已落地：`PageTable` 在 `shared/components/base/`，`shared/request.ts` 有信封解包/401/429 拦截与 X-Trace-Id 上下文注入，`TIMEOUT = { normal: 12_000, judge: 110_000 }`；`modules/qc/` 仅有 1 条 `/qc/tasks` 占位路由，`app/router/index.ts` 已挂载 qcModule。
- 后端 QC 管线（`offline_qc_service.py`）顺序冻结为 ①AC ②窗口 ③规则 ④LLM ⑤Merge ⑥R10 ⑦score ⑧告警，**单次 replay 只有一跳 LLM 调用**，硬超时 `QC_LLM_TIMEOUT=5s`（`config.py:23`），KB 检索 `QC_RAG_INITIAL_TIMEOUT=3s`（`config.py:16`），LLM 降级返回 code=A2011（HTTP 200）。
- 后端角色词表（`app/routers` 的 `require_roles`，无 admin 通配）：reviews=qc_reviewer/supervisor、rules/active=admin/ai_trainer、rules/apply=admin、alerts=supervisor/admin；records/demo/check 端点无角色校验。
- 网关（`server_common.conf.template`）：`/api/v1/qc` 前缀 location `proxy_read_timeout 15s`；已有 judge 正则 location 120s 先例（L83-91），正则优先于前缀匹配。
- 批量接口 `check/batch` 串行循环，`QC_BATCH_MAX=100`。

## Goals / Non-Goals

**Goals:**

- 用最小改动消除 replay/batch 被网关 15s 掐断 504 的风险，不引入异步任务机制。
- 路由 `meta.roles` 与后端 allowlist 三方对齐（§11.1 / §5.0 F / `require_roles`），菜单与守卫同源，无「整页 403」。
- 模块结构、测试形态、trace 上下文串联复用 F1 已验证模式，不发明新范式。

**Non-Goals:**

- replay/batch 后端异步化（留 P1，届时再引入 task_status 轮询机制）。
- 规则编辑/新增/删除（后端无接口，MVP 只读）。
- `ai_trainer` 的规则查看入口（§3.2 未将其归入 QC 模块，其 API 读权限由其他工具承载）。
- 下沉通用 TraceTimeline 到 shared（见 Decisions D4）。

## Decisions

### D1：超时放行照 judge 先例加两个正则 location，不改后端

`^/api/v1/qc/replay/` 与 `^/api/v1/qc/check/batch$` 各放 120s，前端新增 `TIMEOUT.qcReplay = 110_000`（110s < 120s，满足 §9.3「前端必须更小」铁律，留 10s 余量）。

**耗时预算测算**（单次 replay 最坏路径）：KB 检索 3s + LLM 复核 5s + 规则引擎/DB ≈1s ≈ **9s**，远低于 110s；正常路径（LLM 1~3s）约 2~4s。单次 replay 在 15s 默认 location 下仅尾部偶发被掐，但验收冒烟必踩，故一并放行。

**备选方案**：① 先测 P95 再决定——否决，验收期必踩 504，回头补网关仍要走一遍 §13 闭环；② replay 改异步任务+轮询——否决，改动越界且 F2 范围蔓延。

### D2：批量勾选上限锁定为 10

按「单条最坏 9s × 上限 < 110s」核算：20 条最坏 180s 必然超前端 110s；**10 条最坏 90s < 110s，留 20s 余量**。上限写死 10，超出禁用并提示「大批量走后端异步（P1）」。UI 上限是体验约束，不替代后端 `QC_BATCH_MAX=100` 的硬限制。

**备选方案**：不设上限靠 504 报错——否决，同步串行 100 条挂几分钟属不可接受 UX，且失败无部分结果回执；上限 20——否决，最坏路径算不过账。

### D3：三条路由 roles 收窄，回填 SSOT §11.1/§3.2

矛盾根因：§11.1 给 6 条 QC 路由统一 `admin/supervisor/qc_reviewer`，但后端 `require_roles` 显示 reviews 词表**连 admin 都不含**、rules/alerts 词表与矩阵也不一致，且 §5.0 F 标注「已对齐」仅指后端词表修复短码，未回验路由表。收窄方案（纯可见性修正，授权边界仍在后端）：

| 路由 | 收窄后 roles | 依据词表 |
|---|---|---|
| `/qc/rules` | `["admin"]` | rules/active=admin/ai_trainer、apply=admin |
| `/qc/alerts` | `["admin","supervisor"]` | alerts=supervisor/admin |
| `/qc/reviews` | `["supervisor","qc_reviewer"]` | reviews=qc_reviewer/supervisor |

详情页内嵌人审按钮组按 §5.0 注「按钮级按 allowlist」：当前角色 ∉ {qc_reviewer, supervisor} 时置灰+说明，不发起请求。

**备选方案**：保留宽 roles + 403 空态——否决，§15.1 B0003 是偶发越权兜底，不能当整页设计策略；给后端 reviews 词表加 admin——否决，本批边界「不改后端业务代码」，且 admin 是否应复核属业务决策，不擅自扩大。

### D4：TraceTimeline 模块内各自实现，不下沉 shared

complaint 的 `TraceTimeline.vue` 渲染投诉研判事件流；QC trace 事件结构为 `category/name/status/summary` + `langsmith_url`（见 `offline_qc_service._trace`），数据形状与语义均不同。§12 规定 shared/base 组件 MUST 无业务语义，两类时间线的事件标签映射都带业务语义，下沉即违规。QC 在 `modules/qc/components/` 自建时间线组件。

**备选方案**：下沉 shared 做成可配时间线——否决，两个使用方、两种数据形状的抽象属过度工程（三处相似才抽象）。

### D5：X-Trace-Id 复用 F1 拦截器，模块侧只写上下文

`shared/request.ts` 注入逻辑零改动；qc 模块新增 `composables/useQcTraceContext.ts`（照搬 complaint 的 useTraceContext 模式）：QcRecordDetail 挂载时 `setTraceContext`，卸载时清空。串联「查看详情→开检→复核提交」复合链路。

### D6：错误处理只映射白名单内码，其余默认分支

qc 服务 HTTP 状态按 `HTTP_STATUS_BY_CODE` 分发（A2002→422、A2005/A2007→404、A2006→409、A2001/A2003/A2009-A2011→200），前端一律按信封 code 判定成败。模块侧只落实 §15.1 已有动作（A2002 表单标红、A2003 按钮置灰+悬浮、B0004 空态等）；A2004（批量超限）/A2011（LLM 降级）等不在白名单的码走默认分支 message 直出+trace 后 8 位，不新增映射（§15.1 禁令）。

## Risks / Trade-offs

- [网关模板改动需 reload 才生效，冒烟环境若未重载仍会 504] → tasks 验收前置「确认网关已 reload 新 location」，冒烟记录含该步骤证据。
- [批量 10 条上限对运营演示可能偏小] → 提示文案明示 P1 异步化路径；上限常量化，后续调整只改一处。
- [LLM 降级（A2011，HTTP 200）结果与正常结果同信封 200，用户可能误以为质检完整] → 详情页对 `code=A2011` 的响应展示降级标识（llm_status=degraded 已在 trace 与 meta 可见），不阻断人审流程。
- [角色收窄属 SSOT 行为修正，若后端后续给 reviews 词表加 admin 需再开 change 回填] → 可接受，§13 闭环本就是为此设计。
- [demo/pending 与 records 双数据源，任务台列表口径需明确] → 任务台以 demo/pending（待检）+ records（已出分）合并视图，口径在 types.ts 注释与组件 props 命名体现，避免旧页 localStorage 缓存行为回潮。

## Migration Plan

1. 网关模板 +2 location → 部署环境 reload nginx（配置变更，无代码发布）。
2. 前端模块随管理台构建发布；旧 `services/qc-service/web/` 保留至 F4 统一下线（本批不删）。
3. 回滚：前端删 `modules/qc/` 目录即恢复占位路由（反向可拔验证保证）；网关回滚为删除两个 location 块并 reload。两向独立，互不阻塞。

## Open Questions

- records 列表分页参数签名（page/page_size 与 §5.0.1 默认值是否一致）待 types 核实任务确认，不一致走 SSOT 闭环回填，不影响本设计。

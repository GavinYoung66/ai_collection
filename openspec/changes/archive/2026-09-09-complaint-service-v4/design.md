## Context

See proposal.md — Why. V3 静态页与 `/trace`、demo seed、中文按钮已在 `services/complaint-service/web/` 与 8006 API 可用；人审仍以单次 `approve` 为主。事件层已有内存 `publisher`（含 `case.risk.upgraded`）。VerdictType 已含 `agent_violation` / `user_misunderstanding` / `compliant_but_poor_experience` / `pending`。

## Goals / Non-Goals

**Goals:**
- 分步状态可落库/可缓存（与现有 session task_status 对齐或轻量扩展）
- 回电与质检均以 **事件契约** 对接下游；演示 mock 可观测
- UI 文案消除「驳回用户」歧义

**Non-Goals:**
- 真外呼平台、真质检扣分引擎、真 MCP
- LangGraph interrupt 与前端 resume
- 处罚档位 UI

## Decisions

### D1 — 分步状态映射

- **选择**：在现有 `TaskStatus` 上增加或复用清晰中间态，推荐：
  - 定责完成 → `pending_callback`（新枚举值）或文档等价名
  - 回电完成 → `resolved`（可关单）或先 `callback_scheduled` 再关单转 `closed`
- **默认拍板（可实现时微调但须写进 v4-demo）**：`pending_review` →（定责）→ `pending_callback` →（回电事件成功）→ `resolved` →（关单）→ `closed`
- **备选**：不改枚举、仅用 complaint 缓存字段 `disposition_step`；否决原因：列表状态不易讲清

### D2 — API 形态

- **选择**：扩展专用处置路由，避免继续超载单一 `review_action=approve`：
  - `PUT /api/v1/complaints/{id}/disposition/verdict`
  - `PUT /api/v1/complaints/{id}/disposition/callback`
  - 既有 `PUT .../close` 增加前置校验
- **幂等**：同一工单再次 `disposition/callback` → **成功返回已安排，不重复发布** `callback.requested`（除非显式 force，本 change 不做 force）
- **备选**：仅扩展 `ReviewRequest`；否决原因：与「分步可停」语义纠缠

### D3 — 事件名

- 回电：`callback.requested`（新注册到 publisher 允许列表）
- 质检：**固定** `qc.violation.reported`（新事件）；违规定责+处置成功时可**额外**保留 `case.risk.upgraded` 兼容双发（v4-demo 写明）
- payload 强制 `trace_id`（对齐现有 publisher）
- 体验差 / 用户误解：**不**发 `qc.violation.reported`

### D4 — 证据展示分层与顺序

- **定责步（段 A）**：至少提供证据核对入口（详情已有 `/trace` 链路或摘要），落实「以证据为基准」
- **违规清单（段 C）**：成立性为坐席违规后展示有序证据列表；默认 `started_at` 升序，缺失则类别+名称稳定序
- **空列表**：允许 0 条并明示「暂无结构化证据」；质检事件仍可发，`evidence` 为空数组 + 原因摘要说明

### D5 — pending

- UI：横幅引导「重新研判 / 退回 AI 重跑」+ 补证说明（录音/电话/短信）
- API：若提交定责时**仍选择/保持** `pending`（或未给出三类之一）→ 拒绝
- 人审可将结论**改判为**三类之一（须改判原因，因与 AI pending/初选不一致）后进入处置步；**禁止**在成立性未确认为三类之一时关单

### D6 — 实现分段

A 文案+定责 API/UI（含证据入口提示）→ B 分步状态+callback 幂等 → C 证据清单+质检事件 → D pending 横幅强化+文档+smoke/单测

## Risks / Trade-offs

- [新增 task_status 枚举] → 迁移/前端映射表要同步；缓解：v4-demo 明示映射，单测覆盖非法关单
- [双事件 qc + case.risk] → 下游重复；缓解：文档标明职责，mock 可区分 event type
- [无真外呼] → 演示只能证明「已发事件」；缓解：事件列表 API 或 publisher.list_events
- [改判强制原因] → 多一次输入；缓解：仅不一致时必填

## Migration Plan

- 本地/dev：拉代码后重启 8006；旧「通过」按钮移除或隐藏
- 无 DB 强迁移则内存态即可；若 TaskStatus 入 PG 枚举需补 alembic（实现时按现网存储选择，优先最小）
- 回滚：保留 close/review 旧路径开关非本 change 默认；V4 以新路由为准

## Open Questions

- （已关闭）体验差非扣分质检提醒：默认否，仅回电
- （已关闭）回电幂等：再调用不重复发事件
- 改判原因是否落独立审计表：演示写入日志/trace 摘要即可，PG 审计表非本 change 必做

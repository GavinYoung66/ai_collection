## Purpose

Defines a standalone local static QC demo page so operators can seed ended calls, trigger offline QC, inspect hits and clauses, and complete human review in the browser without curl.

## ADDED Requirements

### Requirement: 本地可打开的静态质检前端

系统 SHALL 在 `services/qc-service/web/` 提供无需构建的静态单页（vanilla HTML/JS）。本地 MUST 可用 `python -m http.server 8080`（或文档记载的等价命令）打开。`API_BASE` 默认 `http://localhost:8003`。页眉 MUST 展示并可改 API 地址，MUST 展示 `GET /health` 的 `ac_version` / `llm_degraded` / `rag_connected`。人审 MUST 使用演示 JWT（role=QC），页眉 MUST 可展示或粘贴 token。系统 MUST NOT 要求 React/构建/登录 SPA。系统 MUST NOT 与投诉前端合并为同一应用。

#### Scenario: 本地可打开页面

- **WHEN** 在 `web/` 执行 `python -m http.server 8080` 并用浏览器打开
- **THEN** 可见列表或入口，且前端请求指向 8003 API

### Requirement: 列表灌数与待检点开检

列表 SHALL 提供按钮，文案为「模拟三条待质检通话」。点击 MUST 调用 `POST /api/v1/qc/demo/seed` 并刷新列表。列表 MUST 展示 session 摘要、场景标签、待检或已出分状态。点击待检行 MUST 进入详情并调用 `POST /api/v1/qc/replay/{session_id}`；开检中 MUST 展示加载态并禁止对该行重复触发。点击已出分行 MUST 仅打开详情，MUST NOT 自动重跑。演示主路径 MUST NOT 调用 `POST /qc/replay/raw`。

#### Scenario: 待检行触发 replay

- **WHEN** 用户点击一条待检通话
- **THEN** 发起 `POST /qc/replay/{session_id}`，完成后详情展示分数与 hits

#### Scenario: 已出分行不重跑

- **WHEN** 用户点击已有 `qc_records` 的行
- **THEN** 仅打开详情，不自动再次 replay

### Requirement: 详情展示命中、条款、skip 与人审

详情 MUST 展示 `compliance_score`、`grade`、`risk`、`qc_status`、`qc_result`。命中表 MUST 含来源 `ac|rule|llm`、`rule_id` 或词、证据、扣分（high−20 / medium−8 / low−2；客户侧 R13 MUST 显示不计入）。条款 MUST 展示当场知识库检索返回的原文，MUST NOT 仅用写死脚注充当检索结果。`skipped_rules` MUST 展示，文案 MUST 表明本通未审，MUST NOT 称为已合规。

零扣分时 MUST NOT 展示人审提交按钮，状态 `DONE` 即为完成。有扣分时 MUST 展示：「扣分正确」映射 `CONFIRM`（不改分）；「误报」与「改分」均映射 `CORRECT` 且 MUST 手填 0–100。前端 MUST NOT 暴露 `DISMISS`。`REVIEWED` 后上述按钮 MUST 禁用。

#### Scenario: 合规通无人审

- **WHEN** 详情分数为 100 且无计分 hit
- **THEN** 无人审提交按钮，`qc_status` 为 `DONE`

#### Scenario: 误报必须手填分

- **WHEN** 用户在待复核详情点击「误报」且未填 0–100 新分
- **THEN** 不得提交成功；填入合法分数后 `qc_status` 为 `REVIEWED` 且分数等于所填值

### Requirement: 浏览器主路径不依赖 curl

前端 SHALL 支持：灌数两次 ID 不冲突 → 点合规通出 100 → 点中危或红线通见扣分与条款 → 人审完成。全程 MUST 可在浏览器完成。

#### Scenario: 连续两次灌数

- **WHEN** 用户连续两次成功点击「模拟三条待质检通话」
- **THEN** 至少累计 6 条不同 `session_id`，不得因固定旧 id 导致第二次失败

# complaint-service/v2-review-ui Specification

## Purpose

Defines a minimal local human-review Web UI (static single page) so operators can list, inspect, approve, perform one secondary review action, and close complaint sessions via the existing 8006 APIs without relying on curl.

## Requirements

### Requirement: 本地可打开的静态人审前端

系统 SHALL 在 `services/complaint-service/web/` 提供**无需构建的静态单页**（vanilla HTML/JS）；本地 MUST 可用 `python -m http.server`（或等价静态文件服务）打开；`API_BASE` 默认 `http://localhost:8006`；后端 MUST 按需允许本地前端源的 CORS。

#### Scenario: 本地可打开页面

- **WHEN** 在 `web/` 目录执行 `python -m http.server 8080`（或文档记载的等价命令）并用浏览器打开
- **THEN** 可见列表或入口页，且前端请求指向 8006 API

### Requirement: 列表与详情展示研判结果

前端 SHALL 提供列表页展示投诉 id、摘要或文本片段、`task_status`、`verdict`；详情页 MUST 展示投诉文本、判责/置信度、答辩草稿，以及可折叠的 agent-logs（或等价调试信息）。

#### Scenario: 已研判工单可在详情查看草稿

- **WHEN** 用户打开 `pending_review`（或已出 verdict）工单详情
- **THEN** 可见投诉文本、verdict 与 response_draft（或等价字段）

### Requirement: 浏览器完成复核与关单

前端 SHALL 支持 `approve`，并 MUST 另支持 `reject_rejudge` 或 `escalate` **恰好其一**（实现时固定选择）；复核 MUST 调用现有 `PUT .../review`；对已 `resolved` 工单 MUST 支持关单并调用 `PUT .../close`，状态与 API 一致。

#### Scenario: 浏览器 approve 后状态一致

- **WHEN** 用户在详情页执行 approve
- **THEN** 服务侧状态变为 `resolved`（或 API 约定的通过态），前端展示与 GET 一致

#### Scenario: 浏览器二次复核动作可用

- **WHEN** 用户在详情页执行已实现的 `reject_rejudge` 或 `escalate`
- **THEN** 服务侧状态与 API 约定一致，前端展示与 GET 一致

#### Scenario: 浏览器 close 后状态 closed

- **WHEN** 用户对已 resolved 工单执行 close
- **THEN** 服务侧状态为 `closed`，前端展示与 GET 一致

#### Scenario: 人审主路径不依赖 curl

- **WHEN** 完成列表 → 详情 → approve → close
- **THEN** 全程可在浏览器完成，无需手工 curl 作为验收必要条件

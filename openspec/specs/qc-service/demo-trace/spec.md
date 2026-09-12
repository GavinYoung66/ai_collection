# qc-service/demo-trace Specification

## Purpose

Defines a per-session local QC pipeline timeline for the demo page, with an optional LangSmith deep link that never blocks the local view.

## Requirements

### Requirement: GET trace 返回本地时间线

系统 SHALL 提供 `GET /api/v1/qc/records/{session_id}/trace`（若 records 无行则允许 `GET /api/v1/qc/demo/{session_id}/trace`，实现时固定其一并在任务钉死）。成功信封 `code==="0"`。`data` MUST 含按时间排序的 `events` 数组。事件 MUST 覆盖演示主路径可见步骤：灌数或取源、DP qc-source、词库 AC、R01–R15、LLM、kb/search、打分、出站事件。字段至少含类别、名称、时间、耗时或起止、状态摘要。无事件时 MUST 返回空数组，MUST NOT 5xx。`call_id` MUST NOT 用 `session_id` 冒充。系统 MUST NOT 以 LangSmith 为唯一真相源。时间线 MUST 写明 MCP `call_qc` 本通未调用（其为被投诉查询）。系统 MUST NOT 在质检演示中实现 LangGraph Agent 步骤。

#### Scenario: 开检后时间线可读

- **WHEN** 对已 replay 的 session GET trace
- **THEN** events 至少含 DP 取源、词库或 AC、规则、LLM、search、打分之一组可区分步骤

#### Scenario: 无 LangSmith Key 仍 200

- **WHEN** 环境未配置 LangSmith 密钥
- **THEN** HTTP 200，本地 events 仍可读，无外链或 `langsmith_url` 为空

### Requirement: 可选 LangSmith 外链

当已配置可用 LangSmith 密钥、本次质检存在外部 run/trace 标识、且配置了可解析 URL 模板（含 `{run_id}` 或 `{project}`）时，响应 SHALL 含 `langsmith_url`；前端 MUST 仅在此时显示「在 LangSmith 打开」。缺 Key、缺标识或缺模板时 MUST 隐藏外链且不影响本地时间线。

#### Scenario: 有模板才给外链

- **WHEN** 存在 run id 且配置了 URL 模板
- **THEN** `langsmith_url` 为可打开的 URL

#### Scenario: 缺模板隐藏外链

- **WHEN** 未配置 URL 模板
- **THEN** 不提供可点击外链，本地时间线仍可用

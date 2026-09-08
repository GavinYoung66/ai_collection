## Purpose

Defines complaint-judgment call-chain observability for demos: a local timeline of Agent, MCP, LLM, and RAG steps persisted per complaint, with an optional LangSmith deep link that never blocks the local view.

## ADDED Requirements

### Requirement: 研判路径落盘追踪事件

系统 SHALL 在 `judge(run_graph=true)` 执行路径中，将 Agent / MCP / LLM / RAG（若发生）调用事件**关联写入该投诉可查询存储**（可扩展 agent_logs 或专用 trace 列表）；MUST NOT 仅依赖未落单的进程内 Provider/MCP `call_log` 作为唯一来源。

#### Scenario: 真图后可查询到落盘事件

- **WHEN** 用户对工单完成一次 `run_graph=true` 研判
- **THEN** 随后查询该工单追踪接口，可见至少 Agent 类与 LLM 类事件（有 MCP/RAG 时亦应可见）

### Requirement: 本地链路时间线可读

系统 SHALL 通过 **`GET /api/v1/complaints/{id}/trace`**（默认主契约）提供按时间排序的研判链路时间线；每条事件 MUST 至少包含：时间、类别（Agent / MCP / LLM / RAG / 其他）、名称（节点名或 server.tool 或 model/role）、耗时或起止、成功/失败摘要；详情页右侧「链路追踪」模块 MUST 按时间顺序展示该时间线。既有 `agent-logs` 可保留兼容，但 MUST NOT 作为 V3 时间线唯一/主契约。

#### Scenario: 真图后可见多类调用

- **WHEN** 用户对工单完成一次 `run_graph=true` 研判并打开详情链路追踪
- **THEN** 时间线中至少能区分出 Agent 步骤与 LLM 调用（有 MCP/RAG 时亦展示）

### Requirement: 追踪不依赖 LangSmith 才能演示

本地时间线 MUST 在未配置 LangSmith/LangChain 密钥时仍可用；不得因缺少外部追踪 Key 导致详情页报错或空白阻断演示。

#### Scenario: 无 LangSmith Key 仍可看本地追踪

- **WHEN** 环境未配置 LangSmith 相关密钥
- **THEN** 链路追踪模块仍展示本地时间线（可注明「无外部 Trace 链接」）

### Requirement: 可选 LangSmith 外链

当环境已配置可用的 LangSmith（或文档约定的等价）密钥、本次研判存在外部 run/trace 标识，**且**配置了可解析的外链模板（如 `LANGSMITH_TRACE_URL_TEMPLATE`，含 `{run_id}`/`{project}`）时，系统 SHOULD 在链路追踪模块提供「在 LangSmith 打开」外链；缺 Key、缺标识或**缺模板**时 MUST 隐藏或禁用该入口且不影响本地时间线（避免错误 404）。该能力为 P1，**不作为** V3 浏览器主路径五项必过项。

#### Scenario: 有 trace 时可外链

- **WHEN** 本次研判已记录外部 trace/run id、Key 可用且 URL 模板已配置
- **THEN** 用户可见可点击的 LangSmith（或文档约定）打开入口

#### Scenario: 无模板则隐藏外链

- **WHEN** 存在 run id 但未配置外链模板
- **THEN** 不展示「在 LangSmith 打开」（本地时间线仍可用）

### Requirement: 脱敏与安全

链路追踪对外展示的 prompt/证据摘要 MUST 遵循既有脱敏规则；不得在前端明文展示完整密钥；不得把密钥写入仓库。

#### Scenario: 日志不含密钥

- **WHEN** 查询追踪时间线
- **THEN** 响应与页面不包含 API Key 明文

# complaint-service/v2-seed-data Specification

## Purpose

Defines V2 seed complaint scenarios and enhanced Mock MCP evidence fixtures so true-graph runs get non-empty, distinguishable context without real MCP servers.

## Requirements

### Requirement: V2 可区分种子投诉场景

系统 SHALL 提供至少 3 类可区分的种子投诉场景数据（违规倾向 / 用户误解 / 合规但体验差），每条 MUST 含非空 `complaint_text`、建议关联 `case_id`（或等价案件标识）、以及期望倾向说明；PII MUST 为明显虚构值。

#### Scenario: 三类场景各至少一条

- **WHEN** 检查 V2 种子数据目录或模块
- **THEN** 至少存在三类场景各 ≥1 条，且文案彼此可区分

#### Scenario: 禁止真实客户 PII

- **WHEN** 审查种子中的姓名与电话类字段
- **THEN** 均为虚构样例（如测试号段/假名），不含真实客户标识

### Requirement: V2 Mock 证据与 RAG 非空可观测

系统 SHALL 增强 Mock MCP fixtures（案件信息、ASR/质检片段、法规/答辩模板等），使 `run_graph=true` 路径下证据与 RAG 相关 tool 调用可返回非空、按场景可区分的结果；MUST NOT 要求接入真实业务 MCP Server。

#### Scenario: 真图可读到非空证据上下文

- **WHEN** 使用 V2 种子创建工单并以 `run_graph=true` 触发研判（MCP 仍为 Mock）
- **THEN** agent-logs 或中间产物中可观察到非空证据/法规/模板类结果

#### Scenario: 疑似违规场景有 ASR 或质检片段

- **WHEN** 加载偏 `agent_violation` 的种子对应 Mock 响应
- **THEN** 至少包含一段非空的违规/态度相关 ASR 或质检片段样例

### Requirement: 种子可灌入 8006

系统 SHALL 提供脚本或文档化命令，可将全部 V2 种子工单创建到本地 complaint-service（端口 8006 的 `POST /api/v1/complaints` 或等价批量接口）。

#### Scenario: 一键或少量命令完成灌数

- **WHEN** 按交付脚本/文档执行灌数
- **THEN** 全部 V2 种子工单在服务侧可查询到（列表或按 id GET）

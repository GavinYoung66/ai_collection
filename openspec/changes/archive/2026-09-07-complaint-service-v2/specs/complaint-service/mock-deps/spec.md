## ADDED Requirements

### Requirement: V2 场景级 Mock fixtures 可区分

在既有 14 tool fixture 与种子契约之上，系统 SHALL 为 V2 至少三类业务场景提供可区分的 Mock MCP 成功响应（案件/ASR 或质检/法规或模板至少一类场景差异），供真图路径消费；`USE_MOCK_MCP` MUST 可保持为 true。

#### Scenario: 场景差异可加载

- **WHEN** 分别为违规倾向、用户误解、体验差三类场景加载 Mock 成功响应
- **THEN** 至少一类关键字段（如 ASR 片段、法规条文、模板正文）在场景间可区分且非空

#### Scenario: 不强制真 MCP

- **WHEN** V2 联调使用增强 fixtures
- **THEN** 无需配置真实业务 MCP Server 即可返回成功 payload

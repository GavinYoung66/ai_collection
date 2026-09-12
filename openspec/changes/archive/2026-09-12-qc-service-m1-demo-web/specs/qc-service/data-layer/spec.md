## MODIFIED Requirements

### Requirement: rag 客户端只拉 current.dict

系统 SHALL GET `{QC_RAG_SENSITIVE_BASE}/api/v1/sensitive-words/current.dict`，`text/plain`。超时 SHALL 为 3s，重试 3×，连续 2 次失败熔断 SHALL 保持上一版 AC 自动机。当 `QC_R14_RAG_ENABLED=true` 时，同一 rag 基址 MUST 允许 `POST /api/v1/kb/search`（见 `qc-service/kb-search`）；为 false 时 MUST NOT 调用知识库检索 HTTP。演示默认 `QC_R14_RAG_ENABLED` MUST 为 true。演示默认词库 SHALL 为 rag 约 50 词 `current.dict`，MUST NOT 以 7 词 mock 为默认。

#### Scenario: 词库拉取失败保持旧 AC

- **WHEN** current.dict 连续超时两次
- **THEN** 继续使用上一版 AC 自动机

#### Scenario: 开关关闭时不检索

- **WHEN** `QC_R14_RAG_ENABLED=false`
- **THEN** 不发起 `/kb/search`

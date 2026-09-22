# qc-service/kb-search Specification

## Purpose

Enables qc-service to call rag knowledge-base search so deduction evidence can cite retrieved clauses from data_materials, superseding the M1 ban on POST /kb/search.

## Requirements

### Requirement: qc 作为客户端调用 kb/search

当 `QC_R14_RAG_ENABLED` 为 true 时，qc-service SHALL 在离线管道中对含法律/征信或已合并违规命中的坐席文本调用 `POST {rag}/api/v1/kb/search`（契约对齐 rag-service 现有 search）。演示与本 change 默认 MUST 为 true。检索 query MUST 来自实际 turns，MUST NOT 编造。采用阈值与失败回退：超时、熔断或空结果 SHALL 回退规则包关键词 / 已有 hits，MUST NOT 将 `qc_status` 置为 PARTIAL，MUST NOT 发明新错误频道。本要求覆盖既有 M1「禁止 `/kb/search`」与 `QC_R14_RAG_ENABLED=false` 的演示默认。

#### Scenario: 开关开启时发起检索

- **WHEN** `QC_R14_RAG_ENABLED=true` 且本通存在可检索的坐席违规或法律表述
- **THEN** qc 至少发起一次 `POST /api/v1/kb/search`

#### Scenario: 检索失败不 PARTIAL

- **WHEN** kb/search 超时或返回空
- **THEN** 仍给出 AC+规则（及 LLM）分，`qc_status` 不是因检索失败而变为 PARTIAL

### Requirement: RAG 检索熔断 key 规范化且失败仍出分

qc-service 作为 RAG 客户端调用知识库检索时，熔断/降级标志 MUST 使用 `qc:mcp_degraded:rag_kb`。超时、熔断 open、连接失败或空结果 SHALL 回退规则包关键词 / 已有 hits 并继续打分。系统 MUST NOT 将整单标为失败、MUST NOT 返回 HTTP 500、MUST NOT 仅因检索失败将 `qc_status` 置为 PARTIAL、MUST NOT 发明新错误频道。传输层保持既有 HTTP 检索，MUST NOT 把该客户端改成独立 MCP 网关。

#### Scenario: RAG 熔断仍回退关键词出分

- **WHEN** 检索对端超时或熔断 key `qc:mcp_degraded:rag_kb` 为 open
- **THEN** 管道回退关键词比对，仍给出 AC+规则（及若 LLM 可用则 LLM）分，HTTP 200，不是 500，`qc_status` 不因检索失败变为 PARTIAL

#### Scenario: 检索空结果不整单失败

- **WHEN** kb/search 返回空结果
- **THEN** 与超时相同：回退关键词仍出分，不阻断离线质检

### Requirement: 条款回填 hits

成功检索的条款原文 SHALL 写入该通详情 hits 的条款字段（或等价键，如 `clause` / `kb_snippet`），供前端展示为扣分证据。MUST NOT 仅用前端写死脚注充当检索结果。入库语料 MUST 包含 `data_materials` 中的测试手册 PDF、自律公约 txt、细则 docx 等约定材料。

#### Scenario: 红线通详情可见检索条款

- **WHEN** 红线场景 replay 成功且检索命中
- **THEN** GET records 详情至少一条 hit 含非空条款原文

### Requirement: 词库演示默认约 50 词

演示默认 `QC_RAG_SENSITIVE_BASE` SHALL 指向提供 rag `sensitive_words.dict`（约 50 词）的 `GET /api/v1/sensitive-words/current.dict`，MUST NOT 以 7 词 mock 作为本演示默认。词表校准 MUST 以 `data_materials` 为出处，MUST NOT 另造无关词表。

#### Scenario: AC 可命中材料中的违规词

- **WHEN** 中危或红线样例话术含词表中的词（如找你家人、强制执行、可以减免等）
- **THEN** 合并 hits 中存在 `source=ac` 的命中

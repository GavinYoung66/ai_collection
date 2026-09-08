## Purpose

为 complaint-service 与 rag-service 的投诉法规/处理范本入库及检索质量门槛提供确认文档，对齐 REQ-CJ-005，不改研判链路也不把空检索当本批失败。

## ADDED Requirements

### Requirement: 法规与范本入库确认

系统 SHALL 提供 RAG 确认文档，路径为 `docs/complaint-service/external-deps/rag-kb-confirmation.md`。文档 MUST 分别确认：

- 投诉相关监管法规已入库（或记录缺口）：含 `ingested`、`corpus_count`、`sample_doc_ids`、`owner_team`、`confirmed_date`
- 投诉处理范本已入库（或记录缺口）：含 `ingested`、`template_count`、`sample_template_ids`、`owner_team`、`confirmed_date`

`ingested` 允许 `yes` / `no` / `TBD`；`corpus_count` / `template_count` 允许数字或 `TBD`。文档 MUST 标明阻塞影响：法规未入库则合规判定无法引用 `doc_id`；范本未入库则产出物生成无法带来源 ID。

#### Scenario: RAG 确认文档存在

- **WHEN** 检查 `docs/complaint-service/external-deps/rag-kb-confirmation.md`
- **THEN** 文件存在且为 Markdown

#### Scenario: 法规与范本确认列齐全（表头行）

- **WHEN** 检查 RAG 确认文档中入库相关 Markdown 表头行
- **THEN** 表头行可检索到 `ingested`、`corpus_count`、`sample_doc_ids`、`template_count`、`sample_template_ids`、`owner_team`、`confirmed_date`（`^\|` 表头形态）

### Requirement: RAG 质量门槛记录

系统 SHALL 在同一 RAG 确认文档中记录质量门槛与实测（或 `TBD`）：

| 指标 | 门槛（REQ-CJ-005 / PRD §4.4） |
| --- | --- |
| top-3 准确率 | ≥85% |
| 召回率 | ≥90% |
| 投诉相关法规覆盖率 | ≥95% |

文档 MUST 含列：`metric`、`threshold`、`measured_value`、`dataset`、`method`、`measured_date`、`owner_team`、`gaps`。空检索降级为规则兜底属于已有研判行为，本批只记录，MUST NOT 改图节点。

#### Scenario: 三项门槛数字可检索

- **WHEN** grep RAG 确认文档中的 `85`、`90`、`95` 及指标名 `top-3`、`召回`、`覆盖`
- **THEN** 三项门槛均出现在文档中

#### Scenario: 质量表列齐全（表头行）

- **WHEN** 检查质量门槛 Markdown 表头行
- **THEN** 表头行含 `metric`、`threshold`、`measured_value`、`dataset`、`method`、`measured_date`、`owner_team`、`gaps`

### Requirement: RAG 未就绪时编码侧保持 Mock

系统 SHALL 在 RAG 确认文档中声明：质量门槛未达标或入库未确认时，complaint-service 继续走 Mock MCP `rag_kb` tool；本批 MUST NOT 把 `USE_MOCK_MCP` 改为依赖真实 rag-kb Server。

#### Scenario: 文档声明 Mock 缓解

- **WHEN** 检索 RAG 确认文档的缓解策略
- **THEN** 文档含 Mock MCP / `rag_kb` 继续用于本地开发的声明

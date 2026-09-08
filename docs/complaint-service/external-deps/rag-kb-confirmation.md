# RAG 说明（个人项目 · Ellis Guo）

本地：**Mock rag_kb** 即可跑通研判。负责人：**Ellis Guo**。

## 法规入库

| ingested | corpus_count | sample_doc_ids | owner_team | contact_name | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- | --- |
| yes（Mock） | Mock | mock-doc-1,mock-doc-2,mock-doc-3 | Ellis Guo | Ellis Guo | 2026-09-07 | 本地 Mock，未接真库 |

## 范本入库

| ingested | template_count | sample_template_ids | owner_team | contact_name | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- | --- |
| yes（Mock） | Mock | mock-tpl-1,mock-tpl-2,mock-tpl-3 | Ellis Guo | Ellis Guo | 2026-09-07 | 本地 Mock，未接真库 |

## 质量门槛（目标值，本地不强制实测）

| metric | threshold | measured_value | dataset | method | measured_date | owner_team | gaps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| top-3 准确率 | ≥85% | N/A（Mock） | local-mock | Mock fixture | 2026-09-07 | Ellis Guo | 个人项目不强制评测 |
| 召回率 | ≥90% | N/A（Mock） | local-mock | Mock fixture | 2026-09-07 | Ellis Guo | 个人项目不强制评测 |
| 投诉相关法规覆盖率 | ≥95% | N/A（Mock） | local-mock | Mock fixture | 2026-09-07 | Ellis Guo | 个人项目不强制评测 |

# rag-service 入库与质量确认（14.2 / REQ-CJ-005）

> 法规未入库则合规判定无法引用 `doc_id`；范本未入库则产出物生成无法带来源 ID。
> 质量门槛未达标或入库未确认时，complaint-service 继续走 **Mock MCP** `rag_kb` tool。
> 本批 MUST NOT 把 Mock 改为依赖真实 rag-kb Server，MUST NOT 改图节点。
> TBD ≠ 已确认。

## 法规入库

| ingested | corpus_count | sample_doc_ids | owner_team | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- |
| TBD | TBD | TBD | rag-service 团队 | TBD | 本仓 rag-service 为通用多模态 KB（V5），未见投诉监管法规专项入库确认；需对接方列出法规清单与 doc_id |

阻塞影响：`search_regulations` 空结果 → 合规判定不得编造条款，降级规则兜底（已有研判规划，本批只记录）。

## 投诉处理范本入库

| ingested | template_count | sample_template_ids | owner_team | confirmed_date | gaps |
| --- | --- | --- | --- | --- | --- |
| TBD | TBD | TBD | rag-service 团队 | TBD | 未见投诉答辩/处理范本专项入库确认；需覆盖四值 verdict 场景 |

阻塞影响：`search_response_templates` 无匹配时应返回通用模板；本批不改生成节点。

## 质量门槛（REQ-CJ-005）

| metric | threshold | measured_value | dataset | method | measured_date | owner_team | gaps |
| --- | --- | --- | --- | --- | --- | --- | --- |
| top-3 准确率 | ≥85% | TBD | TBD | TBD（离线评测集 + 人工标注） | TBD | rag-service 团队 | 评测集未建 |
| 召回率 | ≥90% | TBD | TBD | TBD（法规 query 集召回） | TBD | rag-service 团队 | 评测集未建 |
| 投诉相关法规覆盖率 | ≥95% | TBD | TBD | TBD（法规清单 vs 已入库 doc_id） | TBD | rag-service 团队 | 法规金标清单未提供 |

门槛数字：top-3 ≥ **85**%、召回 ≥ **90**%、覆盖 ≥ **95**%。空检索降级为规则兜底，本批不改图。

## 缓解

入库或质量未确认期间，编码侧继续 Mock MCP `rag_kb`（`search_regulations` / `search_response_templates` / `search_complaint_handling_guidelines`）。

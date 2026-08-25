# RAG Summary Metadata Specification

## Purpose

为多模态知识库文档提供"上游预生成、RAG 仅存储/索引/检索返回"的摘要元数据能力，用于提升 OCR 图片、ASR 音频等语义稀疏内容的混合检索命中率，并向调用方返回可溯源摘要。摘要由上游业务中台在文档入库阶段调用大模型预生成，RAG-Service 内部不发起任何 LLM 调用、不生成任何业务话术。

## ADDED Requirements

### Requirement: 摘要元数据注入渠道

系统 SHALL 支持两个摘要注入渠道，摘要文本均由上游业务中台预生成后提交：

1. **上传携带**：`POST /kb/documents` 表单新增可选字段 `summary`（与 file、category 同请求提交）；未携带时摘要为空，行为与 V1/V2/V3 完全一致。
2. **独立补全**：新增接口 `PUT /kb/documents/{doc_id}/summary`（请求体 `{"summary": str}`），用于存量文档、多模态异步入库（OCR/ASR 完成后业务中台方可生成摘要）等后补场景；对不存在或已 archived 的 doc_id 返回 B0004。

摘要 MUST 为文档级摘要（整篇文档语义概述），本版本不支持 chunk 级摘要（chunk 级摘要与向量级摘要聚合检索为后续迭代 Backlog）。`summary` 入参长度 MUST 受上限约束（默认 200 字符，由 `KB_SUMMARY_MAX_LEN` 配置，超限返回 B0001）。

#### Scenario: 上传请求携带摘要成功入库

- **WHEN** 管理员通过 `POST /kb/documents` 上传一张制度截图 JPG 并携带 `summary="该制度规定了逾期协商的申请条件与审批流程"`，category=regulation
- **THEN** 系统返回 doc_id + status=active，摘要被存储（PG 权威 + ES 检索副本）；`KB_SUMMARY_ENABLED=true` 时后续检索可返回该摘要

#### Scenario: 上传未携带摘要保持存量行为

- **WHEN** 管理员通过 `POST /kb/documents` 上传 TXT 文件且不携带 `summary` 字段
- **THEN** 文档按 V1/V2/V3 既有流程正常入库，摘要为空，不产生任何行为差异

#### Scenario: 独立接口补全文档摘要

- **WHEN** 管理员对已入库但无摘要的 doc_id 调用 `PUT /kb/documents/{doc_id}/summary`，body 携带 `{"summary":"该文档为催收话术手册第四版"}`
- **THEN** 系统返回 code=0，文档摘要更新，两端存储（PG 权威 + ES 检索副本）同步刷新，相关查询缓存失效

#### Scenario: 补全不存在的文档返回 B0004

- **WHEN** 管理员对不存在的 doc_id 调用 `PUT /kb/documents/{doc_id}/summary`
- **THEN** 系统返回错误码 B0004（资源不存在），不产生任何写入

#### Scenario: 补全接口索引写入失败返回失败且无半状态

- **WHEN** 管理员对存量 doc_id 补全摘要，ES `doc_summary` 写入失败（如 mapping 未更新），重试 3 次仍失败
- **THEN** 系统返回失败（复用 A3003"写入索引失败"语义），PG `summary` 保持原值，不产生"PG 有摘要、索引无摘要"的半状态，日志记录 warning（含 trace_id）

### Requirement: 摘要元数据存储与同步

系统 MUST 将文档级摘要存储到两端并保证一致性契约（Milvus `kb_vectors` 保持 V3 schema 不变，不向 chunk 向量 metadata 冗余摘要）：

- **PostgreSQL**：`kb_documents` 新增 `summary` 列（TEXT，可空，默认 NULL；存量行不强制回填），为摘要**权威源**。
- **Elasticsearch**：`kb_bm25` 新增 `doc_summary` 字段（text，ik_max_word 分词，参与 BM25 加权检索；mapping 以 `es_v4_mapping.json` 为准），为摘要**检索副本**。

摘要更新（补全接口）后 MUST 同步两端并失效该文档所属 category 的查询缓存；ES 写入失败按降级契约处理（见"摘要缺失与失败降级"Requirement）。

#### Scenario: 上传携带摘要后两端一致

- **WHEN** 管理员上传携带摘要的文档并成功入库
- **THEN** `kb_documents.summary` 有值，ES 该文档索引文档的 `doc_summary` 字段有值，两者内容一致；Milvus 该文档各 chunk 向量不含摘要字段（schema 与 V3 一致）

#### Scenario: 查询文档详情返回摘要

- **WHEN** 管理员调用 `GET /kb/documents/{doc_id}` 查询一个携带摘要的文档
- **THEN** 详情响应包含 `summary` 字段且值与权威源一致；无摘要文档该字段为 null

#### Scenario: 补全摘要后查询缓存失效

- **WHEN** 管理员对某 regulation 分类文档补全摘要后，立即执行同分类查询
- **THEN** 查询缓存已失效，返回结果包含最新摘要，而非缓存中的旧响应

### Requirement: 摘要增强混合检索

系统 SHALL 支持摘要增强检索，由开关 `KB_SUMMARY_ENABLED`（默认 false）控制：

- `results[].summary` 字段 MUST 永远存在（调用方契约稳定），取值规则：开关开启时填充该文档的文档级摘要（无摘要为 null）；开关关闭时一律为 null（不填充摘要内容）。
- 开关开启时：ES 侧对 `doc_summary` 字段执行 BM25 加权召回（加权分 `KB_SUMMARY_ES_BOOST`，默认 1.0，即与正文同权），正文检索结果不受影响。
- 开关关闭时（默认）：检索语义与排序行为与 V3 完全一致，`results[].summary` 恒为 null，不参与任何增强逻辑。
- 摘要增强 MUST 不改变既有双闸门兜底、RRF 融合、Rerank 精排、行级权限、去重等既有检索契约。

#### Scenario: 摘要增强开启时检索返回摘要

- **WHEN** `KB_SUMMARY_ENABLED=true`，管理员检索 query="逾期协商申请条件"，命中携带摘要的 regulation 文档
- **THEN** 响应 `results[]` 中该文档对应项 `summary` 字段填充入库时一致的值，其余检索字段（doc_id/title/chunk_index/content/score/rerank_score）保持既有契约

#### Scenario: 摘要增强开启时无摘要文档正常返回

- **WHEN** `KB_SUMMARY_ENABLED=true`，管理员检索命中一个未携带摘要的存量文档
- **THEN** 该文档对应结果项 `summary` 为 null，不影响命中与排序，不抛异常

#### Scenario: 摘要增强开关关闭时行为与 V3 一致

- **WHEN** `KB_SUMMARY_ENABLED=false`（默认），管理员执行检索
- **THEN** 检索全链路（召回/RRF/Rerank/双闸门/权限/去重/日志/缓存）与 V3 逐项一致，`results[]` 项 `summary` 字段恒为 null（字段存在但无增强语义）

### Requirement: 摘要缺失与失败降级

系统 MUST 保证摘要能力故障不影响既有文档入库与检索主链路：

- 入库时摘要写入失败（ES 索引异常）MUST 不阻断正文入库：按"先正文、后摘要"顺序执行，摘要失败仅记录 warning 日志 + `summary` 置空，正文双索引与 kb_chunks 正常落库；禁止产生"PG 有摘要、索引无摘要"的不一致状态（摘要写入成功才更新 PG summary）。
- 检索时 ES `doc_summary` 字段不可用/未重建 MUST 不改变既有检索行为（按无摘要处理）。
- 降级/兜底响应（双闸门触发、基础设施降级）MUST 不因摘要逻辑改变原有 `is_fallback`/`degraded` 语义。

#### Scenario: 摘要写入失败不阻断正文入库

- **WHEN** 管理员上传携带摘要的文档，ES `doc_summary` 字段写入失败（如 mapping 未更新）
- **THEN** 正文按既有流程正常入库 status=active，摘要置空，日志记录 warning（含 trace_id），不返回 A3003 类错误

#### Scenario: 检索侧摘要字段缺失正常返回

- **WHEN** 管理员检索时 ES 索引尚未重建 `doc_summary` 字段（存量环境）
- **THEN** 检索按无摘要模式正常返回，不因字段缺失抛异常或改变结果集

### Requirement: 摘要生成边界约束

系统 MUST 遵守 PRD §1.4 架构边界：**RAG-Service 内部禁止发起任何 LLM 调用**，摘要文本 MUST 由上游业务中台在文档入库阶段调用大模型预生成后经注入渠道提交；RAG-Service 仅负责摘要的存储、索引、检索返回。系统 MUST NOT 生成面向催收坐席/终端用户的业务回答话术，MUST NOT 恢复任何 `include_answer` 类"生成式应答"字段。

#### Scenario: 摘要能力不引入 RAG 内部 LLM 调用

- **WHEN** 审计或回归检查摘要链路（注入/存储/检索）的调用依赖
- **THEN** 摘要链路不包含任何对 LLM 供应商（如 DashScope 对话接口）的调用，仅依赖既有 embedding-service 向量化与既有基础设施

#### Scenario: 摘要能力边界与范围决策

- **WHEN** 评审 V4 摘要能力的粒度与增强范围
- **THEN** 摘要为文档级（由上游预生成），仅存储于 PG（权威）与 ES（检索副本），Milvus schema 不变；chunk 级摘要（每 chunk 各自语义摘要）与向量级摘要聚合检索（Milvus 独立摘要 collection + 聚合排序）为后续迭代 Backlog；RAG-Service 内部不发起任何 LLM 调用

# RAG 多模态知识库 V4 — 摘要元数据与检索增强（rag-multimodal-kb-v4）

## Why

RAG 多模态知识库 V1/V2/V3 已交付并归档（基础检索、多模态入库、智能检索管道、遗留修复全部闭环），但存在两类未解决问题：

1. **多模态检索命中率短板**：OCR 图片、ASR 音频产出的文本语义稀疏（表格截图、口语转写），仅靠正文向量/BM25 召回命中率低，影响 call-service 等下游消费质量；
2. **19.5「LLM 摘要生成」悬置**：PRD §1.4 边界（RAG-Service 不接 LLM、不生成话术）与 19.5 摘要需求冲突，V3 仅作设计约束记录，未落地。

V4 以「**摘要元数据能力 + 多模态混合检索增强**」为核心：将摘要定位为**检索增强元数据**（非话术生成），由上游业务中台在文档入库阶段调用大模型预生成后随入库提交，RAG-Service 仅负责存储、索引、检索返回——在不触碰 §1.4 边界的前提下提升多模态检索质量；同时闭环 V1-V3 遗留的功能级债务（D7/D8/W 项）。

> **范围聚焦说明**：Explore 阶段曾评估"治理收口为主"路线（版本管理/评估/LangSmith 等治理能力），经需求聚焦后，V4 变更范围收敛为**摘要元数据能力 + 遗留修复**；治理能力、chunk 级摘要、向量级摘要聚合检索均移至 Non-Goals/Backlog（见下），范围收敛不引入任何回归。

## What Changes

**新增能力（核心）— 摘要元数据（summary-metadata）**：

- **摘要注入**：上传接口 `POST /kb/documents` 表单新增**可选**字段 `summary`（由上游业务中台预生成，随入库提交）；新增独立补全接口 `PUT /kb/documents/{doc_id}/summary`（存量文档/多模态异步场景后补摘要）。
- **摘要存储**：PostgreSQL `kb_documents` 新增 `summary` 列（可空，**权威源**）；Elasticsearch `kb_bm25` 新增 `doc_summary` 字段（ik_max_word，**检索副本**，参与 BM25 加权）。Milvus `kb_vectors` **保持 V3 schema 不变**——文档级摘要不冗余到每个 chunk 的向量 metadata（见 Non-Goals）。
- **检索增强**：新增开关 `KB_SUMMARY_ENABLED`（默认 false）。开启时 ES 侧对 `doc_summary` 字段加权召回，检索响应 `results[]` 每项 `summary` 字段填充摘要值；关闭时检索语义与排序与 V3 完全一致，`summary` 字段为 null（字段永远存在，调用方契约稳定）。
- **降级**：摘要缺失/写入失败不阻断正文入库（warning + summary 可空）；摘要写入失败不产生"PG 有摘要、索引无摘要"脏状态（入库顺序契约）。

**遗留修复（V4 范围内）**：

- D7：`main.py` 敏感词 seed 分支复用已关闭 DB session 的构造问题重构（纯代码级）。
- D8：ASR「通话开场白」裁剪由规格超前表述落地为**规则版**实现（转写文本开头问候语关键词识别裁剪，不引入 LLM，不引入新依赖），修改 multimodal-ingest。
- W4/W5/W6：历史文档漂移修正（spec/README/注释对齐代码）。

**明确不做（Non-Goals，标记后续迭代 Backlog）**：

- 治理能力：LangSmith 追踪（含数据出网合规评估）、评估模块、文档版本管理+灰度（FR-KB-004）、Prompt 动态配置、敏感词 CRUD。
- 跨服务：A13 检索日志入 dw 管道（data-platform 侧）、知识图谱（DD-03 复杂版）。
- 摘要粒度与增强范围：chunk 级摘要（每 chunk 各自语义摘要，需按分块语义生成，超出"上游预生成"约束）与向量级摘要聚合检索（Milvus 独立摘要 collection + 聚合排序）均入 Backlog；V4 只做文档级摘要 + ES 字段加权。
- 摘要文本**不由 RAG-Service 内部生成**：本变更不引入任何 RAG 内部 LLM 调用、不恢复 `include_answer` 类字段。

## Capabilities

### New Capabilities

- `rag-service/summary-metadata`: 摘要元数据的完整生命周期——上游预生成摘要的注入渠道（上传携带 + 独立补全接口）、两端存储（PG 权威 + ES 检索）、ES 摘要字段加权检索增强、检索响应摘要返回（`summary` 字段永远存在，开关控制填充）、缺失/失败降级契约。

### Modified Capabilities

- `rag-service/multimodal-ingest`: ASR 转写前新增"通话开场白"规则裁剪（语义级 VAD 规格澄清由"未实现"收敛为"规则版已实现"，仍不引入 LLM 依赖）。

## Impact

- **代码**：`services/rag-service/app/` — `api/documents.py`（上传表单 summary + 补全接口 + 详情返回）、`api/search.py`（响应 summary 填充）、`models/`（document/search Pydantic）、`repositories/doc_repo.py`、`services/ingest_service.py`（摘要入库顺序）、`services/search_service.py`（ES 摘要加权）、`services/summary_service.py`（新增，两端写入编排）、`services/asr_client.py`（开场白规则裁剪）、`main.py`（D7 重构）、`vectorstore/es_client.py`（doc_summary 字段；Milvus 不新增字段）。
- **数据库**：Alembic 迁移（`kb_documents.summary` 列，可空、不强制回填）；ES `kb_bm25` mapping 扩展（`es_v4_mapping.json` + `reindex_es_v4.py`）；**Milvus schema 不变**（V4 不新增字段、不重建索引）。
- **API**（完整对照表见 design.md「API 变更对照表」）：
  - **新增接口**：`PUT /kb/documents/{doc_id}/summary`（补全/更新摘要）。
  - **新增字段**：`POST /kb/documents` form 可选字段 `summary`；`GET /kb/documents/{doc_id}` 详情响应新增可空字段 `summary`；`POST /kb/search` 响应 `results[]` 新增可空字段 `summary`（永远存在，开关关闭为 null）。
  - **完全保持原有不变**：`GET /kb/documents` 列表、`DELETE /kb/documents/{doc_id}`、`GET /kb/documents/{doc_id}/reindex`、`POST /kb/search` 既有请求/响应字段、`GET /sensitive-words`、`/health` 等——路径、响应包、错误码零改动。
  - **向后兼容**：存量调用方不传 `summary` 时行为与 V1/V2/V3 完全一致，零改造。
- **配置**（定义于 `common/config/settings.py`，供全部 7 服务共用）：`KB_SUMMARY_ENABLED`（默认 false）、`KB_SUMMARY_ES_BOOST`（摘要加权分，默认 1.0）、`KB_SUMMARY_MAX_LEN`（摘要长度上限，默认 200）、`KB_ASR_OPENING_TRIM_ENABLED`（开场白裁剪，默认 false）；Dockerfile ENV / `.env.example` 同步。
- **测试**：摘要注入/存储/检索增强/降级用例 + 存量回归（181 passed 基线不回退）；`smoke_rag.sh` 保持原样、新增 V4 冒烟步骤。

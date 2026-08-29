# RAG Hybrid Search Specification (V5 Delta)

## ADDED Requirements

### Requirement: 检索响应扩展 — answer 与 assets 可选字段

系统 SHALL 在 `POST /kb/search` 检索响应中新增两个可选字段，缺省行为与 V4 完全一致：

- **请求**：新增可选入参 `answer?: bool`（缺省 false）。仅当 `KB_LLM_ANSWER_ENABLED=true` **且**请求显式 `answer=true` **且**检索未触发双闸门兜底（返回了有效 results）时，系统才调用 LLM Provider 生成回答；其余情况 MUST NOT 产生任何 LLM 调用。
- **响应 `answer`**（可空，V5 新增）：LLM 开关开启且请求开启且未兜底时，填充带溯源引用的回答文本（引用格式见 `rag-service/llm-answer` 增量规范）；其余情况恒为 null。响应 `degraded` 字段语义扩展：LLM 调用超时/熔断/不可用时 `answer=null` 且 `degraded=true`，仅返回纯检索结果，**不抛错、不 500**。
- **响应 `results[].assets`**（可空数组，V5 新增，缺省为空）：命中 chunk 关联的内嵌图片/表格预览元数据（asset_type(image|table)、page 页码、storage_path 预览定位、mime_type），由内嵌资产抽取（`rag-service/multimodal-ingest` V5 增量）填充；无关联资产时该字段为空数组。
- **兼容性**：存量调用方不传 `answer`、不读 `assets`/`answer` 字段时，检索召回、RRF、Rerank、双闸门、行级权限、去重、缓存、日志语义与 V4 逐项一致，零改造（唯一显式例外：请求显式传 `answer=true` 时跳过查询缓存读取，保证回答可返回，见下文场景）。

#### Scenario: 开关关闭时响应与 V4 完全一致

- **WHEN** `KB_LLM_ANSWER_ENABLED=false`（默认），用户执行检索（无论是否传 answer=true）
- **THEN** 响应不包含 LLM 回答（`answer=null`），不产生任何 LLM 调用，检索行为与 V4 完全一致；`results[].assets` 缺省为空数组

#### Scenario: 开关开启且请求开启时返回带溯源回答

- **WHEN** `KB_LLM_ANSWER_ENABLED=true`，用户检索 query="逾期协商需要哪些材料" 并传 `answer=true`，检索返回有效结果（未触发兜底）
- **THEN** 响应包含 `answer` 字段（回答文本含引用的 doc_id），同时返回完整召回片段 results 与既有溯源字段

#### Scenario: 开关开启但请求未开启时不生成回答

- **WHEN** `KB_LLM_ANSWER_ENABLED=true`，用户检索但未传 `answer`（缺省 false）
- **THEN** 响应 `answer=null`，不产生 LLM 调用，行为与 V4 一致

#### Scenario: 触发双闸门兜底时不生成回答

- **WHEN** `KB_LLM_ANSWER_ENABLED=true` 且用户传 `answer=true`，但检索触发双闸门兜底（results=[]）
- **THEN** 响应按既有兜底契约返回 results=[] + 兜底文案，`answer=null`，不调用 LLM

#### Scenario: LLM 不可用时降级为纯检索结果

- **WHEN** `KB_LLM_ANSWER_ENABLED=true` 且用户传 `answer=true`，检索成功返回结果，但 LLM 调用超时/熔断/不可用
- **THEN** 响应 `answer=null`、`degraded=true`，召回片段 results 正常返回，接口不抛错、不返回 500

#### Scenario: 命中内嵌资产 chunk 时返回预览元数据

- **WHEN** 检索命中一个关联内嵌图片/表格的 chunk（资产抽取已入库）
- **THEN** 响应该结果项 `assets` 包含预览元数据（asset_type、page、storage_path、mime_type），无关联资产的结果项 `assets` 为空数组

#### Scenario: answer=true 跳过缓存读取保证回答返回

- **WHEN** 同一 query 的 V4 检索结果已在查询缓存中，用户再次检索并传 answer=true（开关开启）
- **THEN** 系统跳过缓存读取走真实检索并生成回答，响应 `answer` 有值 + 召回片段完整；`answer=true` 请求不读缓存、不写缓存（动态内容），存量调用方（不传 answer）缓存行为与 V4 完全一致

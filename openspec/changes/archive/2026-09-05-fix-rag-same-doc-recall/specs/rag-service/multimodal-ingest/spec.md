## MODIFIED Requirements

### Requirement: 内嵌表格结构化解析（PDF/DOCX/PPTX）

系统 SHALL 支持从 PDF、DOCX、PPTX 中解析内嵌表格并**保留行列结构**，由 `KB_TABLE_EXTRACT_ENABLED`（默认 false）门控，关闭时=表格按既有文本化处理、V4 行为：

- **独立门控**：本开关只决定表格资产。`KB_TABLE_EXTRACT_ENABLED=false` 时 MUST NOT 产生结构化表格 chunk，也 MUST NOT 产生 `asset_type=table` 的 `kb_doc_assets` 记录，即使 `KB_IMAGE_EXTRACT_ENABLED=true`；
- **结构化入库**：表格按"保留行列结构、不打散"原则解析，每行序列化为文本 chunk 入库（含表格上下文前缀），问答可命中表格**任意单元格**；
- **章节可定位（本变更）**：当抽取开关开启时，每一行序列化文本 MUST 带上可定位上下文，不得只有页码。可定位上下文 MUST 为该表最近的章节标题、文档标题或表名之一（例如 `表格(第0页)[3.1 RAG多模态知识库系统]: 技术类别:…`）。无标题可抽取时 MUST 使用表头拼接或「未命名表」占位，不得省略前缀结构；
- **列汇总 chunk（本变更）**：每个成功解析的表格 MUST 额外写入至少 1 条汇总 chunk，内容包含该表的可定位上下文，以及至少一列（优先标识列，如「技术类别」「子系统」）的全部非空取值列表，使「某章节有哪些技术类别」可被单条 chunk 回答。汇总 chunk 失败不得阻断行 chunk 入库；
- **元数据**：表格解析记录 MUST 落 `kb_doc_assets`（asset_type=table，含 doc_id、页码 page、storage_path、ocr_text 序列化文本、mime_type、content_json 行列结构化 JSON）；content_json 为表格预览渲染的单一数据源；
- **预览**：检索命中表格 chunk 时，响应 `results[].assets` MUST 返回该表格预览元数据（asset_type=table、页码 page、storage_path、mime_type）；content_json 不随检索 API 传输，由 Gradio 经共享数据卷读取；
- **容错**：合并单元格/不规则表格按既有启发式收敛（取左上角表头 + 合并行值保留），解析失败 MUST 不影响正文入库（warning + 跳过该表格）；单表失败 MUST skip，不得 rollback 整单；解析超时沿用 `KB_PARSE_TIMEOUT`；
- **回滚契约**：表格文本 chunk 走既有七步流水线，回滚契约与 V1 一致。
- **存量**：本变更只约束新入库（及用户显式重新抽取资产的路径）。不强制批量回填已 active 文档；存量需重新上传或等价重建资产后才带章节前缀与列汇总。

#### Scenario: 内嵌表格入库且问答命中任意单元格

- **WHEN** 管理员上传一个含"还款计划表"（列：期数/还款日/应还金额/状态）的 DOCX，`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 表格以行列结构化 chunk 入库，检索"第 3 期应还金额"可命中对应行 chunk，返回的表格预览以行列结构展示，不被打散

#### Scenario: 表格行带章节或表名

- **GIVEN** DOCX 中表格位于章节「3.1 RAG多模态知识库系统」下，`KB_TABLE_EXTRACT_ENABLED=true`
- **WHEN** 文档入库完成
- **THEN** 该表每行 chunk 的 content 同时含可定位章节/表名与单元格 `列:值`；存在至少一行 content 同时匹配该章节名（或等价表名）与 `技术类别:`

#### Scenario: 表格产生列汇总 chunk

- **GIVEN** 表格含「技术类别」列且有多行取值（如多模态文档解析、语义分块、向量化）
- **WHEN** 文档入库完成
- **THEN** kb_chunks 中存在至少 1 条汇总 chunk，content 含该表可定位上下文且列出上述技术类别取值

#### Scenario: 表格预览随检索结果返回

- **WHEN** 管理员检索命中一个含内嵌表格 chunk，`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 响应 `results[].assets` 返回该表格预览元数据（asset_type=table、页码、storage_path 预览定位），Gradio 经共享数据卷读取 content_json 文件以表格视图渲染

#### Scenario: 表格解析失败不影响正文入库

- **WHEN** 管理员上传的 PDF 中某表格解析失败（异常结构），`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 该表格被跳过并记录 warning，正文文本正常入库 status=active，不整体失败

#### Scenario: 表格抽取开关关闭保持 V4 行为

- **WHEN** `KB_TABLE_EXTRACT_ENABLED=false`（默认），管理员上传含表格的 PDF
- **THEN** 系统按既有文本化方式处理表格区域（表格文本混入正文），不产生结构化表格 chunk 与 kb_doc_assets 记录，行为与 V4 完全一致

#### Scenario: 只开图不开表不产生结构化表资产

- **GIVEN** 文档同时含图片与表格
- **WHEN** `KB_IMAGE_EXTRACT_ENABLED=true` 且 `KB_TABLE_EXTRACT_ENABLED=false`
- **THEN** 不产生结构化表格 chunk，也不产生 `asset_type=table` 的 `kb_doc_assets` 记录；图片按本 capability 图片条款处理

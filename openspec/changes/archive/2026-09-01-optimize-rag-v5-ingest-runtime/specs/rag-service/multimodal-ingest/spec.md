## MODIFIED Requirements

### Requirement: 内嵌图片抽取与文搜图（PDF/DOCX/PPTX）

系统 SHALL 支持从 PDF、DOCX、PPTX 中抽取内嵌图片并实现**文搜图**（文本查询命中返回对应图片），由 `KB_IMAGE_EXTRACT_ENABLED`（默认 false）门控，关闭时=仅文本解析、V4 行为：

- **抽取范围**：内嵌 PNG/JPEG 位图直接抽取；矢量图（SVG 类/绘图对象）经渲染器（PyMuPDF/Pillow）渲染为位图后抽取；**DOCX 浮动图、粘贴图、环绕图**（含 OOXML `wp:anchor` 及文档包媒体部件，不限于嵌入式 `wp:inline`）在本开关开启时 MUST 抽出；同一图片内容（含嵌入路径与媒体补漏同时命中）MUST 只入一份；
- **独立门控**：本开关只决定图片资产。`KB_IMAGE_EXTRACT_ENABLED=false` 时 MUST NOT 产生 `asset_type=image` 的 `kb_doc_assets` 记录、图片文件与 `source=asset_image` 的 chunk，即使 `KB_TABLE_EXTRACT_ENABLED=true`；
- **文搜图实现形态**：抽取图片经 ocr-service:8200（PaddleOCR）识别文本，OCR 文本以普通 chunk 走既有清洗/分块/双索引链路入库（source=`asset_image`），实现文本索引的文搜图；**图片向量（CLIP 类）检索为 Backlog，本版本不实现**；
- **元数据**：图片抽取记录 MUST 落 `kb_doc_assets`（asset_type=image，含 doc_id、页码 page、storage_path、ocr_text、mime_type）；图片文件落本地磁盘 `KB_ASSETS_DIR`；
- **预览**：检索命中 asset 关联的 chunk 时，响应 `results[].assets` MUST 返回该图片预览元数据（含页码、存储定位），供 Gradio 前端预览；
- **容错**：单张图片抽取/OCR 失败 MUST 不影响整篇文档正文入库（warning + 跳过该资产），且 MUST NOT 因处理该失败而对共享 DB Session 做全量 rollback（以免回滚 `kb_documents` 并阻断同文档其余表格/图片资产写入）；解析超时沿用 `KB_PARSE_TIMEOUT`；
- **回滚契约**：图片 OCR 文本作为 chunk 走既有七步流水线，PG 成功但索引失败 → 回滚 + status=failed + A3003，与 V1 一致。

#### Scenario: PDF 内嵌图片抽取入库并被文本查询命中

- **WHEN** 管理员上传一个含流程图内嵌图片的 PDF 制度文件，`KB_IMAGE_EXTRACT_ENABLED=true`
- **THEN** 图片被抽取 → OCR 识别出"逾期协商流程图"等文字 → 以 chunk 入库（source=asset_image），检索"逾期协商流程图"可命中该 chunk，返回结果含 doc_id 与页码元数据，且 `results[].assets` 包含该图片预览信息

#### Scenario: 矢量图渲染为位图后走 OCR 索引

- **WHEN** 管理员上传的 PPTX 中包含矢量绘制的组织结构图，`KB_IMAGE_EXTRACT_ENABLED=true`
- **THEN** 矢量图被渲染为位图后抽取，OCR 识别文字并以 chunk 入库，可被文本查询命中

#### Scenario: 图片抽取失败不影响正文入库

- **WHEN** 管理员上传的 PDF 中某张内嵌图片抽取/OCR 失败（其余页面正文正常）
- **THEN** 该图片被跳过并记录 warning（含 trace_id），正文文本正常入库 status=active，不整体失败

#### Scenario: 图片抽取开关关闭保持 V4 行为

- **WHEN** `KB_IMAGE_EXTRACT_ENABLED=false`（默认），管理员上传含内嵌图片的 PDF
- **THEN** 系统仅做文本解析入库，不抽取图片、不产生 kb_doc_assets 记录，行为与 V4 完全一致

#### Scenario: DOCX 浮动或粘贴图在抽图开启时抽出

- **GIVEN** 管理员上传一份 DOCX，其中图片为浮动/粘贴/环绕（非仅嵌入式 inline）
- **WHEN** `KB_IMAGE_EXTRACT_ENABLED=true`
- **THEN** 该图片被抽出并落 `kb_doc_assets`（asset_type=image）与本地文件，OCR 文本可按既有文搜图链路入库；不得因非 inline 而漏抽

#### Scenario: 只开表不开图不产生图片资产

- **GIVEN** 文档同时含图片与表格
- **WHEN** `KB_IMAGE_EXTRACT_ENABLED=false` 且 `KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 不产生 image 资产、图片文件与 `source=asset_image` 的 chunk；表格按本 capability 表格条款处理

### Requirement: 内嵌表格结构化解析（PDF/DOCX/PPTX）

系统 SHALL 支持从 PDF、DOCX、PPTX 中解析内嵌表格并**保留行列结构**，由 `KB_TABLE_EXTRACT_ENABLED`（默认 false）门控，关闭时=表格按既有文本化处理、V4 行为：

- **独立门控**：本开关只决定表格资产。`KB_TABLE_EXTRACT_ENABLED=false` 时 MUST NOT 产生结构化表格 chunk，也 MUST NOT 产生 `asset_type=table` 的 `kb_doc_assets` 记录，即使 `KB_IMAGE_EXTRACT_ENABLED=true`；
- **结构化入库**：表格按"保留行列结构、不打散"原则解析，每行序列化为 `列名1:值1, 列名2:值2,...` 文本 chunk 入库（含表格上下文前缀），问答可命中表格**任意单元格**；
- **元数据**：表格解析记录 MUST 落 `kb_doc_assets`（asset_type=table，含 doc_id、页码 page、storage_path、ocr_text 序列化文本、mime_type、content_json 行列结构化 JSON）；content_json 为表格预览渲染的单一数据源（供 Gradio gr.Dataframe 直接渲染，不依赖文本还原）；
- **预览**：检索命中表格 chunk 时，响应 `results[].assets` MUST 返回该表格预览元数据（asset_type=table、页码 page、storage_path 预览定位、mime_type）；表格行列结构 `content_json` 不随 API 传输，由 Gradio 经共享数据卷按 storage_path 指向的 content_json 文件读取渲染（不依赖文本还原），供前端以表格形式展示（不被打散成零散文本）；
- **容错**：合并单元格/不规则表格按既有启发式收敛（取左上角表头 + 合并行值保留），解析失败 MUST 不影响正文入库（warning + 跳过该表格）；解析超时沿用 `KB_PARSE_TIMEOUT`；
- **回滚契约**：表格文本 chunk 走既有七步流水线，回滚契约与 V1 一致。

#### Scenario: 内嵌表格入库且问答命中任意单元格

- **WHEN** 管理员上传一个含"还款计划表"（列：期数/还款日/应还金额/状态）的 DOCX，`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 表格以行列结构化 chunk 入库，检索"第 3 期应还金额"可命中对应行 chunk，返回的表格预览以行列结构展示，不被打散

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

## ADDED Requirements

### Requirement: 资产 chunk 批量索引写入

系统 MUST 将同一文档、同一次资产处理中的图片 OCR chunk 与表格行 chunk 以**批量**方式完成 embedding 与双索引写入（复用正文侧批量 embedding / 批量双写），MUST NOT 对每个资产 chunk 发起一次完整的「单条 embedding + 单条双写」往返。

检索语义不变：表格仍为**每行一个文本 chunk**，图片 OCR 仍为独立 chunk；本 Requirement 只约束写入路径的批量性，不合并、不拆散检索粒度。回滚契约与既有七步流水线一致（资产 chunk **批量** embedding 或双写失败 MUST 回滚索引 + status=failed + A3003）。

**Session / 单资产容错（与图片「单张失败不影响正文」条款对齐并收紧实现边界）：**

- 单张图片 OCR / 单表准备失败 MUST 仅 warning + skip 该资产，MUST NOT 对与 `kb_documents` 共享的 DB Session 做全量 `rollback()`；
- 进入资产批量写入 `kb_chunks` / `kb_doc_assets` 之前，对应 `kb_documents` 行 MUST 已可被外键引用（正文阶段后显式 commit，或等价已提交边界）；
- 本条款 **不削弱** 批量写入失败时的 A3003 回滚契约；不起 OCR 服务不得作为入库成功的前置条件。

#### Scenario: 多行表格一次完成向量与双写

- **GIVEN** `KB_TABLE_EXTRACT_ENABLED=true`，文档含一张不少于 10 行的内嵌表
- **WHEN** 系统为这些行写入索引
- **THEN** 每行仍各对应一个 chunk 可被检索命中；写入 MUST 走批量 embedding 与批量双写，不得按行串行完成整次索引往返

#### Scenario: 多张图片 OCR chunk 批量写入

- **GIVEN** `KB_IMAGE_EXTRACT_ENABLED=true`，文档抽出不少于 2 张图片且 OCR 均有文本
- **WHEN** 系统为这些 OCR 文本写入索引
- **THEN** 各图片仍为独立 chunk；写入 MUST 走批量 embedding 与批量双写

#### Scenario: 批量写入失败回滚与正文一致

- **WHEN** 资产 chunk 批量 embedding 或双写失败
- **THEN** 按既有契约回滚索引，文档 status=failed，返回 A3003，不得留下部分资产索引

#### Scenario: OCR 不可用时含图表文档仍成功入库

- **GIVEN** `KB_IMAGE_EXTRACT_ENABLED=true` 且 `KB_TABLE_EXTRACT_ENABLED=true`，文档同时含内嵌图片与内嵌表格；OCR 服务不可达或单张 OCR 失败
- **WHEN** 管理员上传该文档完成入库
- **THEN** 正文入库成功且 status=active；失败图片仅 skip + warning，不产生因 Session 全量 rollback 导致的 `kb_chunks`/`kb_documents` 外键错误；表格资产与表格行 chunk 仍按批量路径入库并可被检索命中；整单 MUST NOT 仅因 OCR 失败而 B0007/整单失败

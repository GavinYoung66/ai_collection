# RAG Multimodal Ingest Specification

## Purpose

为催收业务知识的非文本载体提供多模态解析入库能力：图片经 OCR 独立服务识别文字、音频经 ASR Provider 转写文字，两者产出的纯文本无缝接入 V1 既有的清洗、分块、向量化、双索引链路，复用 IngestService 状态机与回滚契约。

## Requirements

### Requirement: 图片 OCR 解析支持

系统 SHALL 支持 .png / .jpg 格式图片上传，通过 PaddleOCR 独立服务（endpoint `/ocr`，`lang="ch"` 中英文混合）提取图片文字；识别失败时按降级策略处理（见下方"OCR/ASR 解析失败降级"Requirement）：默认（开关关闭）走统一 A3001 文档解析失败分支，与 V2 行为一致。OCR 调用携带 30s 超时、3 次指数退避、3 连失败熔断标识 `rag:ocr:degraded`（TTL 60s）。

#### Scenario: 制度截图 OCR 成功入库

- **WHEN** 管理员上传一张包含"逾期协商流程"的 JPG 制度截图，category=regulation
- **THEN** OCR 服务返回文字提取结果，经清洗分块写入双索引，文档 status=active，后续以该图文字检索可命中对应 chunk

#### Scenario: OCR 服务熔断降级

- **WHEN** PaddleOCR 连续 3 次失败后，管理员再上传 PNG 文件，且降级开关关闭（默认）
- **THEN** 系统直接短路调用，返回 A3001，status=failed，并在 60s 内所有图片上传均走熔断短路径

#### Scenario: OCR 服务熔断且降级开关开启

- **WHEN** PaddleOCR 连续 3 次失败后，管理员再上传 PNG 文件，且 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 系统不返回 A3001，文档按"占位入库"降级策略处理（见下方降级 Requirement），status=failed

#### Scenario: 纯空白图无文字

- **WHEN** 管理员上传一张纯白无文字 PNG 文件
- **THEN** OCR 返回空文本，分块服务产生 0 有效 chunk，走 A3002 文本切片失败，status=failed

### Requirement: 音频 ASR 转写支持

系统 SHALL 支持 .mp3 / .wav / .m4a / .amr / .wma 格式音频上传，通过 ASR Provider 抽象（DashScope Paraformer 默认，Whisper 可切换）执行语音转文字；**MUST 在转写前执行 VAD 分片**：对整段音频执行静音裁剪（去除首尾静音段），并按 ≤ 60s 时长切分为多个分片，逐片转写后按时间顺序拼接结果；**MUST 在转写文本拼接完成后执行"通话开场白"规则裁剪（审计 D8 落地）**：对转写文本开头进行催收通话开场模式规则识别（如"喂 / 你好 / 您好 / 请问是 / 先生 / 女士 / 我们是 XX 银行（公司）/ 您的信用卡逾期"等问候与身份确认关键词），命中则裁剪至首个有效业务内容起点；规则裁剪为纯规则实现（正则 + 关键词表，配置于 `conf/asr_opening_rules.yaml`），**不引入 LLM 依赖、不引入新第三方依赖**；语义级 VAD（说话人/意图级开场识别）仍为后续迭代 Backlog，不影响本次规则裁剪；提取失败 A3001，超时 60s、3 次重试、熔断 `rag:asr:degraded`。ASR Provider 抽象 MUST 支持至少两个可切换实现：DashScope Paraformer（默认）与 Whisper（本地），Whisper 实现 MUST 可完成 WAV/MP3 转写并返回与 DashScope 一致的转写文本契约，切换 `ASR_PROVIDER` 配置不影响对外请求/响应契约。

**V5 扩展（AMR/WMA）**：系统 SHALL 支持 .amr / .wma 电话录音格式。解码策略：先经 **ffmpeg**（服务端二进制依赖，pydub 封装）解码为标准 WAV，再复用既有 VAD 静音裁剪 + ≤60s 分片 + ASR Provider 转写管线（**Client 侧解码，Provider 侧保持接收 WAV/音频流契约不变**，即 AMR/WMA 接入不改变 ASR Provider 抽象对外契约）；`KB_ASR_AMR_WMA_ENABLED`（默认 false）门控。损坏/无法解码的 AMR/WMA MUST 返回业务错误（A3001，或 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走既有降级占位分支），**不得卡死/崩溃 worker、不得影响后续请求**。M4A 保持既有支持不变。

#### Scenario: 通话录音转写入库成功

- **WHEN** 管理员上传一段 90s 催收通话 MP3 录音，category=case
- **THEN** 系统按 60s 分片 → 逐片去静音转写 → 拼接转写文本（开场白裁剪开关开启时对文本开头进行规则裁剪）→ 分块写入双索引，文档 status=active，kb_documents.source 字段记录为 "asr"

#### Scenario: 90s 录音 VAD 分片后逐片转写

- **WHEN** 管理员上传 90s 通话 WAV 文件
- **THEN** 系统将音频裁剪静音后切分为至少 2 个 ≤60s 分片，逐片调用 Provider 转写，最终入库文本为各分片转写结果按时间顺序拼接的完整内容，且不丢失任一分片文本

#### Scenario: 短静音音频单分片转写

- **WHEN** 管理员上传 20s 无静音 WAV 文件
- **THEN** VAD 检测无静音段、总时长 ≤60s，系统以单个分片直接转写，不强制切分，结果与整段转写一致

#### Scenario: ASR Provider 切换为 Whisper 后转写正常

- **WHEN** 管理员在配置中切换 ASR_PROVIDER=whisper 后上传 WAV 文件，Whisper 模型可用
- **THEN** 请求/响应契约无变化，系统内部调用 Whisper Provider 完成转写，转写结果仍按 V1/V2 既有清洗规则去空行与脏数据，开场白规则裁剪对转写文本同样生效

#### Scenario: Whisper Provider 无 Key 或模型不可用时降级

- **WHEN** ASR_PROVIDER=whisper 但本地模型加载失败或转写异常
- **THEN** 系统按既有 ASR 异常路径返回 A3001（或降级开关开启时的占位入库），不抛未捕获异常，不影响服务其他接口

#### Scenario: 损坏音频解析失败

- **WHEN** 管理员上传损坏加密 M4A 文件
- **THEN** ASR 调用抛出异常，系统返回 A3001，status=failed，不写入任何索引

#### Scenario: AMR 录音 ffmpeg 解码后正常转写入库

- **WHEN** 管理员上传一段 .amr 电话录音且 `KB_ASR_AMR_WMA_ENABLED=true`
- **THEN** 系统经 ffmpeg 解码为 WAV 后走既有 VAD 分片 + ASR Provider 转写管线，转写文本分块写入双索引，status=active，source=asr

#### Scenario: WMA 录音正常转写入库

- **WHEN** 管理员上传一段 .wma 电话录音且 `KB_ASR_AMR_WMA_ENABLED=true`
- **THEN** 系统经 ffmpeg 解码后走同一转写管线，正常入库并可被检索命中

#### Scenario: 损坏 AMR 返回业务错误且 worker 不卡死

- **WHEN** 管理员上传一个损坏/无法解码的 .amr 文件
- **THEN** 系统返回 A3001（或降级开关开启时走占位入库分支），处理该文件的 worker 不卡死、不崩溃，后续上传请求可正常处理

#### Scenario: 开关关闭时 AMR/WMA 按既有白名单外处理

- **WHEN** `KB_ASR_AMR_WMA_ENABLED=false`（默认），管理员上传 .amr 文件
- **THEN** 系统按格式白名单外处理返回 A3001，行为与 V4 完全一致

#### Scenario: 通话开场白规则裁剪

- **WHEN** 管理员上传一段以"喂，你好，请问是李女士吗？我们是XX公司法务部"开头的催收通话录音，且 `KB_ASR_OPENING_TRIM_ENABLED=true`
- **THEN** 入库转写文本中该开场白语句被裁剪，首条有效业务内容（如"您有一笔逾期账单需要处理"）成为文本起点，其余转写内容不变

#### Scenario: 开关关闭时开场白保留原样

- **WHEN** 管理员上传含开场白的通话录音，且 `KB_ASR_OPENING_TRIM_ENABLED=false`（默认）
- **THEN** 转写文本与 V3 行为完全一致，开场白不被裁剪，不影响既有分块与入库

#### Scenario: 无开场白音频不受影响

- **WHEN** 管理员上传一段直接从业务内容开始的 20s 录音（如"您有一笔逾期账单"），且开场白裁剪开关开启
- **THEN** 规则识别不命中，转写文本原样入库，不误删任何有效业务内容

### Requirement: OCR/ASR 解析失败降级

当 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`（默认 false=V2 原行为）时，系统 SHALL 在 OCR/ASR 解析阶段失败（服务未部署、Key 未配置、熔断、超时、解析异常）时，不再直接返回 A3001 失败，而是执行以下降级策略：

1. **占位入库**：将文档标记 status=failed 并落一条解析失败日志（含原因），不写任何索引，返回 A3001 的语义保留在日志中；
2. 降级响应 MUST 在返回/日志中标记 `degraded=true`，供监控与排查；
3. 熔断标识（`rag:ocr:degraded` / `rag:asr:degraded`）在降级开启时仍正常置位与 TTL 过期。

> **规格收敛（审计 D3）**：早期 spec 曾描述"仅文本入库"策略（请求附带纯文本时跳过 OCR/ASR 直接入库，status=active）；当前上传接口（`POST /kb/documents`，multipart file+category）无纯文本附带参数，该分支不可触发，V3 不实现。"仅文本入库"收敛为占位入库（唯一实现策略），如后续接口扩展附带文本能力，再迭代实现。

当开关关闭（默认值），行为与 V2 完全一致：OCR/ASR 解析失败直接 A3001 + status=failed。

#### Scenario: OCR 服务未部署时占位失败

- **WHEN** ocr-service 未部署，管理员上传 JPG 制度截图，`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 系统不调用 OCR，文档 status=failed，不写索引，落解析失败日志，响应标记 degraded=true（"仅文本入库"未实现，见降级 Requirement 规格收敛说明）

#### Scenario: ASR 无 Key 且无附带文本时占位失败

- **WHEN** DashScope Key 未配置、无附带文本，`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **THEN** 文档 status=failed，不写索引，落解析失败日志，接口返回 code="0" 且 data 标记 degraded=true（不抛 500）

#### Scenario: 降级开关关闭保持 V2 行为

- **WHEN** `KB_OCR_ASR_DEGRADED_KEEP_TEXT=false`（默认），OCR 服务不可用
- **THEN** 系统按 V2 原行为返回 A3001、status=failed，不产生任何降级入库

### Requirement: 外部解析器状态机与回滚复用

OCR / ASR 解析产出纯文本后，系统 SHALL/MUST 复用 V1 IngestService 的后续 7 步流水线（clean → processing 占位 → split → batch_embed → Milvus+ES 双写 → kb_chunks 持久化 → status=active）；多存储回滚契约与 V1 完全一致（PG 成功但索引失败 → MUST 回滚索引 + status=failed + 返回 A3003）。VAD 分片、Whisper 转写、OCR/ASR 降级策略均为解析阶段的扩展，不改变本 Requirement 定义的回滚契约。

#### Scenario: OCR 文本成功但 Embedding 不可用

- **WHEN** PNG OCR 提取 1000 字，但 Embedding 熔断返回异常
- **THEN** IngestService 按 V1 既有流程 rollback：不写 Milvus/ES/kb_chunks，文档 status=failed，返回 A3003，行为与 V1 DOCX 解析成功但 Embedding 失败时完全一致

#### Scenario: ASR 转写成功但 Milvus 半写失败

- **WHEN** MP3 转写成功、分块、批量 Milvus 写入一半崩溃
- **THEN** Milvus delete_by_doc 清理已写入向量 + ES 回滚 + kb_chunks 清理，文档 status=failed，禁止 PG=active 但索引脏数据状态

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

### Requirement: ASR Key 可用性判定与错误定位

系统 SHALL 在音频转入 ASR 转写前判定当前所选 ASR Provider 所需的凭据是否真实可用，避免"已配置可用 Key 却仍判空"的误报：

- **可用性判定**：当所选 Provider 为 DashScope 时，MUST 读取 `DASHSCOPE_API_KEY`（含等价的阿里千问 DashScope Key）；当该 Key 非空且有效时，MUST 正常调用 DashScope ASR 完成转写，**不得抛「DASHSCOPE_API_KEY not configured」**；仅当所选 Provider 所需 Key 确实为空时才判定"未配置"。
- **错误定位**：当确无可用 Key 而抛 A3001 时，异常/错误信息 MUST 明确指明缺失的是哪个配置项（如 `DASHSCOPE_API_KEY`），便于定位；不得模糊为笼统的"文档解析失败"。
- **降级兼容**：Key 缺失时的行为 MUST 与既有「OCR/ASR 解析失败降级」契约一致——`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走占位入库（status=failed + degraded=true，不抛 500），默认关闭时返回 A3001 + status=failed。

本 Requirement 为既有「音频 ASR 转写支持」的兼容性修正，不改变既有 VAD 分片、≤60s 分片、开场白裁剪、Whisper 可切换、熔断 `rag:asr:degraded` 等既有行为。

#### Scenario: 已配置 DashScope Key 时 mp3 转写成功（不再 A3001）

- **GIVEN** 环境中已配置有效的 `DASHSCOPE_API_KEY`（或等价千问 DashScope Key），`ASR_PROVIDER=dashscope`
- **WHEN** 管理员上传一段 90s 催收通话 `.mp3`，category=case
- **THEN** 系统调用 DashScope ASR 转写成功，经清洗、分块写入双索引，返回 doc_id + status=active，source=asr，不再返回 A3001「DASHSCOPE_API_KEY not configured」

#### Scenario: 未配置任何可用 Key 时返回可定位的 A3001

- **GIVEN** `DASHSCOPE_API_KEY` 为空，`ASR_PROVIDER=dashscope`，降级开关关闭（默认）
- **WHEN** 管理员上传 `.mp3` 文件
- **THEN** 系统返回 A3001，status=failed，且错误信息明确提示缺失 `DASHSCOPE_API_KEY`

#### Scenario: 降级开关开启时 Key 缺失走占位入库

- **GIVEN** `DASHSCOPE_API_KEY` 为空，`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true`
- **WHEN** 管理员上传 `.mp3` 文件
- **THEN** 系统不抛 500，文档 status=failed、不写索引、落解析失败日志，响应标记 degraded=true

#### Scenario: 切换 Whisper 且模型可用时转写正常

- **GIVEN** `ASR_PROVIDER=whisper`，本地模型加载成功
- **WHEN** 管理员上传 `.mp3` / `.wav` 文件
- **THEN** 系统调用 Whisper Provider 完成转写，对外请求/响应契约不变，处理过程与既有行为一致

### Requirement: 资产阶段与异步入库进度协作

当图/表抽取开关开启且文档含资产时，资产处理（落盘、OCR、表格准备、批量索引）MUST 在文档已返回 `processing` 之后的后台任务中执行，并推进可观测进度至 `asset_index`（与 document-ingest 进度字段语义一致）。单张 OCR / 单表准备失败仍 MUST 仅 skip + warning，MUST NOT 全量 rollback 共享 Session；资产批量 embedding/双写失败仍 MUST 回滚索引 + status=failed + A3003（不削弱既有契约）。

资产文件落 `KB_ASSETS_DIR` 后，MUST 通过 `GET /kb/documents/{id}` 响应中的 `assets` 元数据供 Gradio 在上传页/列表侧预览 image（不新建公网静态下载 API）。

#### Scenario: 含图文档后台推进 asset_index

- **GIVEN** `KB_IMAGE_EXTRACT_ENABLED=true`，上传含内嵌图的 DOCX 已返回 processing
- **WHEN** 后台进入资产处理
- **THEN** 进度可见 asset_index（或等价）；完成后 status=active 或因批量失败 failed；抽出的 image 文件可在共享盘按 storage_path 读取

#### Scenario: OCR 不可用时正文与表格仍可完成（异步）

- **GIVEN** 图/表开关开启，OCR 不可达；文档含图与表
- **WHEN** 后台入库跑完
- **THEN** 失败图片 skip + warning；正文与表格按既有容错可达 active；不得因 OCR 导致整单仅 B0007 或永久 processing

### Requirement: 重复上传防护（同名/同 hash 仍 processing）

系统 SHALL 在同一文件仍处于 `processing` 时阻止重复入库。**后端 MUST** 拒绝「同 title（文件名）且 status=processing」的重复 `POST /kb/documents`（见 document-ingest「重复上传后端拒绝」）。Gradio MUST 在检测到进行中同名 processing 文档时阻止或强提示。文件 content hash 检测为可选增强，非本版 MUST。

#### Scenario: processing 同名再上传被拦或强提示

- **GIVEN** 已有 title 为 `a.docx` 且 status=processing 的文档
- **WHEN** 用户再次上传同名 `a.docx`（Gradio 或 API）
- **THEN** Gradio 阻止或强烈提示；API 返回明确业务错误；不得在无提示下静默启动第二条并行入库


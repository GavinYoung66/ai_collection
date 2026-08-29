# RAG Multimodal Ingest Specification (V5 Delta)

## MODIFIED Requirements

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

## ADDED Requirements

### Requirement: 内嵌图片抽取与文搜图（PDF/DOCX/PPTX）

系统 SHALL 支持从 PDF、DOCX、PPTX 中抽取内嵌图片并实现**文搜图**（文本查询命中返回对应图片），由 `KB_IMAGE_EXTRACT_ENABLED`（默认 false）门控，关闭时=仅文本解析、V4 行为：

- **抽取范围**：内嵌 PNG/JPEG 位图直接抽取；矢量图（SVG 类/绘图对象）经渲染器（PyMuPDF/Pillow）渲染为位图后抽取；
- **文搜图实现形态**：抽取图片经 ocr-service:8200（PaddleOCR）识别文本，OCR 文本以普通 chunk 走既有清洗/分块/双索引链路入库（source=`asset_image`），实现文本索引的文搜图；**图片向量（CLIP 类）检索为 Backlog，本版本不实现**；
- **元数据**：图片抽取记录 MUST 落 `kb_doc_assets`（asset_type=image，含 doc_id、页码 page、storage_path、ocr_text、mime_type）；图片文件落本地磁盘 `KB_ASSETS_DIR`；
- **预览**：检索命中 asset 关联的 chunk 时，响应 `results[].assets` MUST 返回该图片预览元数据（含页码、存储定位），供 Gradio 前端预览；
- **容错**：单张图片抽取/OCR 失败 MUST 不影响整篇文档正文入库（warning + 跳过该资产）；解析超时沿用 `KB_PARSE_TIMEOUT`；
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

### Requirement: 内嵌表格结构化解析（PDF/DOCX/PPTX）

系统 SHALL 支持从 PDF、DOCX、PPTX 中解析内嵌表格并**保留行列结构**，由 `KB_TABLE_EXTRACT_ENABLED`（默认 false）门控，关闭时=表格按既有文本化处理、V4 行为：

- **结构化入库**：表格按"保留行列结构、不打散"原则解析，每行序列化为 `列名1:值1, 列名2:值2,...` 文本 chunk 入库（含表格上下文前缀），问答可命中表格**任意单元格**；
- **元数据**：表格解析记录 MUST 落 `kb_doc_assets`（asset_type=table，含 doc_id、页码 page、storage_path、ocr_text 序列化文本、mime_type、content_json 行列结构化 JSON）；content_json 为表格预览渲染的单一数据源（供 Gradio gr.Dataframe 直接渲染，不依赖文本还原）；
- **预览**：检索命中表格 chunk 时，响应 `results[].assets` MUST 返回该表格预览元数据（含 content_json 行列结构），供 Gradio 前端以表格形式展示（不被打散成零散文本）；
- **容错**：合并单元格/不规则表格按既有启发式收敛（取左上角表头 + 合并行值保留），解析失败 MUST 不影响正文入库（warning + 跳过该表格）；解析超时沿用 `KB_PARSE_TIMEOUT`；
- **回滚契约**：表格文本 chunk 走既有七步流水线，回滚契约与 V1 一致。

#### Scenario: 内嵌表格入库且问答命中任意单元格

- **WHEN** 管理员上传一个含"还款计划表"（列：期数/还款日/应还金额/状态）的 DOCX，`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 表格以行列结构化 chunk 入库，检索"第 3 期应还金额"可命中对应行 chunk，返回的表格预览以行列结构展示，不被打散

#### Scenario: 表格预览随检索结果返回

- **WHEN** 管理员检索命中一个含内嵌表格 chunk，`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 响应 `results[].assets` 返回该表格预览元数据（含 content_json 行列结构、页码），Gradio 前端直接以表格视图渲染

#### Scenario: 表格解析失败不影响正文入库

- **WHEN** 管理员上传的 PDF 中某表格解析失败（异常结构），`KB_TABLE_EXTRACT_ENABLED=true`
- **THEN** 该表格被跳过并记录 warning，正文文本正常入库 status=active，不整体失败

#### Scenario: 表格抽取开关关闭保持 V4 行为

- **WHEN** `KB_TABLE_EXTRACT_ENABLED=false`（默认），管理员上传含表格的 PDF
- **THEN** 系统按既有文本化方式处理表格区域（表格文本混入正文），不产生结构化表格 chunk 与 kb_doc_assets 记录，行为与 V4 完全一致

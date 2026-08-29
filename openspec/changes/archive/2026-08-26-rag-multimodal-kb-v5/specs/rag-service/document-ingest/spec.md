# RAG Document Ingest Specification (V5 Delta)

## MODIFIED Requirements

### Requirement: 文档上传与自动入库

系统 SHALL 接受 PDF、DOCX、MD、TXT、PNG、JPG、JPEG、MP3、WAV、M4A、XLSX、PPTX、AMR、WMA 格式文件上传，自动执行：解析提取文本（PDF/DOCX/MD/TXT 本地 Parser；XLSX 走 Excel 解析器；PPTX 走 PPT 解析器；PNG/JPG/JPEG 走 PaddleOCR 独立服务 /ocr；MP3/WAV/M4A/AMR/WMA 走 ASR Provider 抽象）→ 清洗（去空行、多余空格、页眉页脚、音频静音裁剪）→ 分块 → 向量化（1024dim）→ 写入 Milvus 向量库和 Elasticsearch BM25 索引 → 写入 PostgreSQL kb_chunks 表 → 更新文档状态为 active。格式白名单以外（含 .exe）返回 A3001。

**V5 扩展**：新增 `.xlsx` / `.pptx` / `.amr` / `.wma` 四种格式，全部复用 V1 七步流水线（clean→split→embed→Milvus+ES 双写→kb_chunks 持久化→active）；`kb_documents.source` 字段 MUST 打标区分来源：`.xlsx`→`excel`、`.pptx`→`ppt`、`.amr`/`.wma`→`asr`（与既有 OCR/ASR 打标一致）；多存储回滚契约与 V1 完全一致（PG 成功但索引失败 → 回滚已写入索引 + status=failed + A3003）；既有格式（txt/md/pdf/docx/png/jpg/jpeg/mp3/wav/m4a）解析行为与 V4 完全一致，不得因新格式引入回归。

#### Scenario: 上传 TXT 文档成功

- **WHEN** 管理员上传一个 TXT 文件并指定 category=regulation
- **THEN** 系统返回 doc_id、chunks_count > 0、status=active，且 kb_chunks 表有对应记录，Milvus 和 ES 可检索到该文档内容

#### Scenario: 上传 PDF 文档成功

- **WHEN** 管理员上传一个 PDF 文件
- **THEN** 系统提取文本（过滤页眉页脚），分块后向量化写入双索引，返回 status=active

#### Scenario: 上传不支持格式

- **WHEN** 管理员上传一个 .exe 文件
- **THEN** 系统返回错误码 A3001（文档解析失败），HTTP 200，统一响应包 code 不为 "0"

#### Scenario: 上传制度截图 JPG 成功

- **WHEN** 管理员上传制度流程截图 .jpg，category=regulation
- **THEN** 系统调用 PaddleOCR /ocr 提取文本，经清洗分块向量化写入双索引，返回 doc_id + status=active

#### Scenario: 上传 JPEG 扩展名图片成功

- **WHEN** 管理员上传 .jpeg 扩展名的制度截图，category=regulation
- **THEN** 系统将其识别为 OCR 格式白名单，调用 PaddleOCR /ocr 提取文本，走与 .jpg 相同的入库链路，返回 doc_id + status=active

#### Scenario: 上传通话录音 MP3 成功

- **WHEN** 管理员上传催收通话录音 .mp3，category=case
- **THEN** 系统调用 ASR Provider 转写文本（静音裁剪 + 分片拼接），经清洗分块向量化写入双索引，返回 doc_id + status=active

#### Scenario: 上传 Excel 文档成功且来源打标

- **WHEN** 管理员上传一个含多 sheet 的 .xlsx 台账文件，category=script
- **THEN** 系统按 Excel 解析器提取全部 sheet 文本，分块后向量化写入双索引，返回 doc_id + status=active，kb_documents.source 记录为 "excel"

#### Scenario: 上传 PPT 文档成功且来源打标

- **WHEN** 管理员上传一个 .pptx 培训演示文稿，category=regulation
- **THEN** 系统按 PPT 解析器提取每页文本（含演讲者备注），分块后向量化写入双索引，返回 doc_id + status=active，kb_documents.source 记录为 "ppt"

#### Scenario: 上传 AMR 通话录音成功且来源打标

- **WHEN** 管理员上传一个 .amr 电话录音，category=case
- **THEN** 系统经 ffmpeg 解码后走 ASR Provider 转写，分块后向量化写入双索引，返回 doc_id + status=active，kb_documents.source 记录为 "asr"

#### Scenario: 新增格式入库失败回滚与 V1 一致

- **WHEN** 管理员上传 .xlsx 文件，解析分块成功但 Embedding 服务熔断
- **THEN** 系统按 V1 既有回滚契约：清理已写入的 PG/Milvus/ES，status=failed + A3003，行为与既有 DOCX 格式 Embedding 失败完全一致

### Requirement: 分类隔离

系统 MUST 通过 category 字段（regulation/script/case/other）实现知识分类，文档上传时必填 category，检索时可按 category 过滤。

#### Scenario: 按分类过滤检索

- **WHEN** 检索时指定 category=regulation
- **THEN** 返回结果只包含 category=regulation 的文档片段，不返回 script/case/other 类片段

## ADDED Requirements

### Requirement: Excel 表格文档解析（.xlsx）

系统 SHALL 支持 .xlsx 格式解析入库，解析规则 MUST 满足：

- **全部 sheet 入库**（由 `KB_EXCEL_SHEET_ALL` 配置，默认 true；false 时仅首个 sheet）；
- **首行 = 表头**（表头行号由 `KB_EXCEL_HEADER_ROW` 配置，默认 1）；
- **行切块**：除表头行外每行数据序列化为 `"列名1:值1, 列名2:值2,..."` 的文本 chunk；
- **sheet 名作前缀冗余进每行 chunk**（前缀格式 `sheet名: 列名1:值1,...`），用于区分同名列的不同 sheet 与提升检索命中；
- 空行、全空值行 MUST 跳过，不产生 chunk；
- 解析超时沿用 `KB_PARSE_TIMEOUT`（默认 30s），超时按既有解析失败路径处理（A3001 + status=failed）；
- 损坏/加密 .xlsx 文件按 A3001 处理。

#### Scenario: 多 sheet Excel 全部入库且可命中具体行

- **WHEN** 管理员上传含 "逾期费率表" 与 "协商申请材料" 两个 sheet 的 .xlsx，`KB_EXCEL_SHEET_ALL=true`（默认）
- **THEN** 两个 sheet 均被解析入库，检索 "逾期费率表 年利率 0.05" 可命中对应行 chunk，且该 chunk 文本以 sheet 名前缀开头

#### Scenario: sheet 名前缀冗余进每行 chunk

- **WHEN** 管理员上传含 sheet "客户名单" 的 .xlsx，某行内容为 `张三|138xxxx|已逾期`
- **THEN** 该行入库 chunk 文本形如 `客户名单: 姓名:张三, 电话:138xxxx, 状态:已逾期`，sheet 名作为前缀冗余在其中

#### Scenario: 空行与全空值行跳过

- **WHEN** 管理员上传的 .xlsx 中存在整行为空、或整行所有单元格均为空的记录行
- **THEN** 这些行不产生任何 chunk，不影响其余有效行的入库

#### Scenario: 损坏 Excel 返回 A3001

- **WHEN** 管理员上传一个损坏或加密的 .xlsx 文件
- **THEN** 系统返回 A3001，status=failed，不产生部分索引残留

### Requirement: PPT 演示文稿解析（.pptx）

系统 SHALL 支持 .pptx 格式解析入库，每页 MUST 抽取：标题 + 正文（文本框）+ 演讲者备注（备注文本并入该页文本一起入库），多页文本按页顺序拼接后走既有分块链路；系统 MUST 过滤页码/版式占位文字（如页脚页码数字、版式自带的"点击添加标题"等占位提示），过滤采用**保守启发式**（仅过滤确定性的页码模式与已知占位文案），不得误删正文；解析超时沿用 `KB_PARSE_TIMEOUT`，损坏 .pptx 按 A3001 处理。

#### Scenario: PPT 备注内容可检索命中

- **WHEN** 管理员上传一个 .pptx，其中第 3 页演讲者备注包含"该费率仅适用于 90 天内逾期"
- **THEN** 备注文本并入该页文本入库，检索 "90 天内逾期费率" 可命中该页对应 chunk

#### Scenario: 页码/版式占位不产生独立 chunk

- **WHEN** 管理员上传一个每页含页码"1/2/3..."与版式占位"点击添加标题"的 .pptx
- **THEN** 入库文本中页码与版式占位文字被过滤，不产生以纯页码或占位文案为内容的独立 chunk

#### Scenario: 正常正文不被启发式误删

- **WHEN** 管理员上传的 .pptx 中正文自然包含类似数字的内容（如"第 3 期还款"）
- **THEN** 启发式过滤不命中该正文，正文文本完整入库

### Requirement: 内嵌图片与表格资产存储与删除清理（kb_doc_assets）

系统 MUST 提供内嵌资产（图片/表格）的元数据存储与生命周期管理：

- **存储**：新增表 `kb_doc_assets`（id / doc_id FK CASCADE / asset_type(image|table) / page(INT) / storage_path / ocr_text / mime_type / content_json TEXT（表格行列结构化 JSON，图片为 NULL）/ 公共字段），记录文档、页码、存储定位、OCR 文本与结构化预览数据；
- **图片文件**：落**本地磁盘** `KB_ASSETS_DIR`（默认 `services/rag-service/data/assets/`，Docker 挂载 volume），storage_path 记录相对路径，供 Gradio 预览；
- **删除清理**：删除文档时 MUST 同步删除该文档在 `kb_doc_assets` 的全部记录并清理本地磁盘对应资产文件，失败按"删除失败重试与补偿"既有契约处理（有限重试 + 告警标记，不得静默成功造成残留）；
- **reindex**：重新解析入库时 MUST 重建该文档的 assets（先清旧再写新）；
- 新表为增量表，不影响既有 kb_documents/kb_chunks 语义。

#### Scenario: 资产元数据落表与本地存储

- **WHEN** 管理员上传一个含 3 张内嵌图片与 1 个内嵌表格的 PDF，图片抽取开关开启
- **THEN** kb_doc_assets 新增 4 条记录（3 条 asset_type=image + 1 条 asset_type=table），图片文件落本地磁盘 KB_ASSETS_DIR，记录含 doc_id 与页码；表格记录 content_json 保存行列结构化数据供前端渲染

#### Scenario: 删除文档同步清理资产

- **WHEN** 管理员删除一个已抽取内嵌图片/表格的文档
- **THEN** 该文档 kb_doc_assets 记录全部删除，本地磁盘对应资产文件被清理，kb_documents 状态变更为 archived

#### Scenario: reindex 重建资产

- **WHEN** 管理员对含内嵌资产的文档执行 reindex
- **THEN** 旧的 assets 记录与本地文件先清理，再按新解析结果重建，不产生旧资产残留

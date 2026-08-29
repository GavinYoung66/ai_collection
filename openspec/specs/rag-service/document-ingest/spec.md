# RAG Document Ingest Specification

## Purpose

支持催收业务知识文档（PDF/DOCX/MD/TXT/PNG/JPG/JPEG/MP3/WAV/M4A）上传后自动解析、清洗、分块、向量化并写入 Milvus 和 Elasticsearch 双索引，支持单文档增量删除和重新入库，禁止全量重建。

## Requirements

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

### Requirement: 文档状态生命周期管理

系统 MUST 管理文档状态流转：processing（入库中）→ active（可用）或 failed（入库失败）。archived 表示已归档删除。

#### Scenario: 入库成功状态流转

- **WHEN** 文档上传并成功完成全链路入库
- **THEN** 文档状态从 processing 变为 active

#### Scenario: 入库失败状态流转

- **WHEN** 向量化服务不可用导致入库失败
- **THEN** 文档状态变为 failed，并记录错误日志

### Requirement: 增量删除

系统 SHALL 支持按 doc_id 删除单个文档：删除 PostgreSQL kb_chunks 对应记录、删除 Milvus 中该文档的所有向量、删除 ES 中该文档的所有索引文档、更新文档状态为 archived。禁止全量重建知识库。

#### Scenario: 删除文档后索引清理

- **WHEN** 管理员删除一个已入库的文档
- **THEN** Milvus 搜索该文档内容返回空，ES 搜索该文档内容返回空，kb_chunks 表该文档记录被软删除，kb_documents 状态变为 archived

### Requirement: 重新解析入库

系统 SHALL 支持对已入库文档执行重新解析：先删除旧索引（Milvus + ES + PG chunks），再重新走完整入库流程。

#### Scenario: 重新入库后内容更新

- **WHEN** 管理员对文档执行 reindex 操作
- **THEN** 旧的 chunks 和向量被清除，新的 chunks 和向量被写入，文档状态变为 active

### Requirement: 分类隔离

系统 MUST 通过 category 字段（regulation/script/case/other）实现知识分类，文档上传时必填 category，检索时可按 category 过滤。

#### Scenario: 按分类过滤检索

- **WHEN** 检索时指定 category=regulation
- **THEN** 返回结果只包含 category=regulation 的文档片段，不返回 script/case/other 类片段

### Requirement: 脏数据过滤

系统 MUST 过滤无效分块：字符长度小于 10 的片段直接丢弃，不写入索引。

#### Scenario: 短片段被过滤

- **WHEN** 文档分块后产生一个长度为 5 个字符的片段
- **THEN** 该片段不写入 Milvus、ES 和 kb_chunks 表

### Requirement: Embedding 失败可选降级入库（开关默认关闭=V1 行为）

当 `KB_EMBED_DEGRADED_KEEP_TEXT=true`（默认 false=V1 原行为）时，若 batch_embed 调用失败，系统 SHALL 降级为仅写入 ES 文本索引（跳过 Milvus 向量、跳过 kb_chunks.vector_id 为空），文档状态仍为 active；kb_search_logs 对应文档检索时 degraded=true 并溯源。当开关关闭（默认值），行为与 V1 完全一致（Embedding 失败 → 回滚索引 + status=failed + A3003）。

#### Scenario: 开启降级，Embedding 宕机仍可文本检索

- **WHEN** KB_EMBED_DEGRADED_KEEP_TEXT=true，文档解析成功但 Embedding 连续熔断
- **THEN** 系统仅写 ES 索引，不写 Milvus，kb_chunks.vector_id=null，文档 status=active；BM25 检索可命中该文档，但 Milvus 向量召回不可用（ degraded=true ）

#### Scenario: 开关关闭（默认）= V1 失败路径

- **WHEN** KB_EMBED_DEGRADED_KEEP_TEXT=false，Embedding 失败
- **THEN** 系统按 V1 既有回滚契约：清理 PG/ES/Milvus 已写入，status=failed + A3003，与 V1 现有行为完全一致

### Requirement: ES 入库失败降级（开关默认关闭=V2 行为）

当 `KB_ES_DEGRADED_KEEP_MILVUS=true`（默认 false=V2 原行为）时，系统 SHALL 在入库阶段 ES 索引写入失败时降级为**仅 Milvus 入库**：保留 Milvus 向量写入与 kb_chunks 持久化，跳过 ES index_docs，文档 status 仍置 active，并记录 degraded 标记与补索引重试任务（异步按指数退避重试 ES 写入，重试窗口 30min，成功则补写 ES；窗口内重试仍失败则置 warning 日志，不改变文档状态）。当开关关闭（默认值），行为与 V2 完全一致：ES 写入失败 → 回滚 PG/Milvus 已写入 + status=failed + 返回 A3003。

#### Scenario: 开启降级，ES 宕机仍可向量检索

- **WHEN** `KB_ES_DEGRADED_KEEP_MILVUS=true`，文档解析分块成功但 ES index_docs 连续失败
- **THEN** 系统仅写 Milvus + kb_chunks，文档 status=active；检索时向量召回可命中该文档，BM25 召回不可用（degraded=true）；ES 在 30min 窗口内异步补索引成功

#### Scenario: 降级窗口内重试仍失败

- **WHEN** ES 持续宕机超过 30min，补索引重试全部失败
- **THEN** 系统停止重试并记录 warning 日志（含 doc_id、失败原因、时间），文档维持 active 仅向量可检索，不阻塞后续上传

#### Scenario: 开关关闭（默认）= V2 失败路径

- **WHEN** `KB_ES_DEGRADED_KEEP_MILVUS=false`，ES 写入失败
- **THEN** 系统按 V2 既有回滚契约：清理 PG/Milvus 已写入，status=failed + 返回 A3003，与 V2 现有行为完全一致

### Requirement: 上传大小上限

系统 MUST 限制单次上传文件大小：超过 `KB_MAX_FILE_SIZE`（默认 20MB，对齐 DD-03 §5.1/§8）的上传请求返回 B0001（参数错误）。大小校验 MUST 在将文件内容读入内存之前完成，避免超大文件先占内存再被拒绝。

#### Scenario: 超过上限返回 B0001

- **WHEN** 管理员上传一个 25MB 的文件
- **THEN** 系统返回错误码 B0001，不产生任何入库写入，服务内存占用不受文件实际大小影响

#### Scenario: 边界值放行

- **WHEN** 管理员上传一个恰好 20MB 的文件
- **THEN** 系统按既有流程正常解析入库，返回 doc_id + status=active

### Requirement: 解析超时控制

系统 MUST 对文档解析（文本提取）设置超时上限 `KB_PARSE_TIMEOUT`（默认 30s，对齐 DD-03 §6.1/§8）：解析超时按既有解析失败路径处理（返回 A3001、文档状态置 failed；`KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 时走既有降级分支），不得让请求无限挂起。

#### Scenario: 解析超时按失败处理

- **WHEN** 文档解析耗时超过 KB_PARSE_TIMEOUT 仍未完成
- **THEN** 系统中止解析，返回 A3001，文档状态为 failed，不产生部分索引残留

#### Scenario: 正常解析不受影响

- **WHEN** 文档解析在超时阈值内完成
- **THEN** 系统按既有流程继续分块、向量化并入库

### Requirement: 健康检查依赖探活

系统 MUST 提供 `GET /health` 端点并真实探测 PostgreSQL、Milvus、Redis、Embedding 四项依赖的可用性（对齐 DD-03 §2）；任一依赖不可用时健康状态为非 healthy（响应字段契约以全局 step1/01 /health 规范为准）。健康检查探活异常 MUST NOT 导致接口返回 500。重复的 /health 端点 MUST 统一为单一实现，避免双端点行为漂移。

#### Scenario: 全部依赖正常

- **WHEN** PostgreSQL、Milvus、Redis、Embedding 均可用
- **THEN** /health 返回 healthy 状态

#### Scenario: 单依赖不可用

- **WHEN** Milvus 不可用
- **THEN** /health 返回非 healthy 状态，HTTP 状态码与响应结构遵循全局 step1/01 /health 规范

#### Scenario: 探活超时不抛 500

- **WHEN** 任一依赖探活调用超时
- **THEN** /health 判定该依赖不可用并返回非 healthy，接口不抛 500

### Requirement: 删除失败重试与补偿

系统 MUST 保证增量删除收敛：删除文档时若 Milvus/ES 删除操作失败，系统 MUST 有限重试（指数退避）；重试仍失败 MUST 记录告警日志（含 doc_id、失败目标、原因）并标记待补偿，不得静默返回成功造成「PG 已 archived 而向量/索引残留」。

#### Scenario: 删除失败重试成功

- **WHEN** Milvus 删除首次失败、重试后成功
- **THEN** 系统返回成功，最终三端（PG/Milvus/ES）均无该文档残留

#### Scenario: 重试仍失败记录告警

- **WHEN** ES 删除重试仍失败
- **THEN** 系统返回业务成功（不阻断删除），但记录告警日志并标记该文档待补偿，供运维后续人工清理

### Requirement: ES 降级补索引任务持久化

在 `KB_ES_DEGRADED_KEEP_MILVUS=true` 的降级场景下，系统 MUST 持久化补索引任务状态（待补文档清单与重试进度）；进程重启后 MUST 恢复并继续补索引，不得因重启丢失导致 ES 长期缺文档。

#### Scenario: 进程重启后恢复补索引

- **WHEN** ES 降级期间服务进程重启
- **THEN** 重启后系统依据持久化状态继续补写 ES，不丢失待补文档

#### Scenario: 重试窗口内完成补索引

- **WHEN** ES 在 30min 重试窗口内恢复
- **THEN** 补索引完成，ES 与 Milvus/PG 两端一致

### Requirement: 重复删除语义约定（非幂等）

系统 MUST 对不存在的 doc_id 与已 archived（已删除）的 doc_id 统一返回 B0004（资源不存在）；重复删除不视为成功。该行为为既有契约的显式固化（对齐 document-ingest 主 spec 既有行为与 DD-03 §5.1 修订后表述）。

#### Scenario: 删除不存在的文档

- **WHEN** 管理员删除一个不存在的 doc_id
- **THEN** 系统返回错误码 B0004，不产生任何写入

#### Scenario: 重复删除已归档文档

- **WHEN** 管理员再次删除一个已 archived 的文档
- **THEN** 系统返回错误码 B0004，不产生副作用

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

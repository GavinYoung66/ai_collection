## MODIFIED Requirements

### Requirement: 文档上传与自动入库

系统 SHALL 接受 PDF、DOCX、MD、TXT、PNG、JPG、JPEG、MP3、WAV、M4A、XLSX、PPTX、AMR、WMA 格式文件上传，自动执行：解析提取文本 → 清洗 → 分块 → 向量化（1024dim）→ 写入 Milvus 与 Elasticsearch → 写入 PostgreSQL kb_chunks → 更新文档状态为 active。格式白名单以外（含 .exe）返回 A3001。

**异步契约（BREAKING）**：`POST /kb/documents` 在请求校验（格式/大小/category/summary 等）通过后，MUST 尽快返回统一成功信封，且 `data` 含 `doc_id` 与 `status=processing`（本变更选定 **HTTP 200 + status=processing**，不使用 202）。解析、正文索引、资产索引 MUST 在后台继续；完成后文档 status MUST 变为 `active` 或 `failed`。调用方 MUST NOT 再假设上传响应即为 `active` 或含最终 `chunks_count`（最终 chunks_count 以列表/详情为准）。校验失败仍同步返回业务错误码，不得创建后台任务。

**V5 扩展**保持不变：`.xlsx`/`.pptx`/`.amr`/`.wma` 复用七步流水线与 source 打标；多存储回滚契约与既有一致（索引失败 → 回滚已写入索引 + status=failed + A3003，可在后台任务中兑现）。既有格式解析行为不得因异步引入回归。

#### Scenario: 上传校验通过后立即返回 processing

- **WHEN** 管理员上传合法 TXT 并指定 category=regulation，校验通过
- **THEN** 系统在解析/索引完成前返回 HTTP 200，`data.doc_id` 非空且 `data.status=processing`；列表可查到该文档为 processing

#### Scenario: 上传 TXT 文档成功

- **WHEN** 管理员上传一个 TXT 文件并指定 category=regulation
- **THEN** 系统先返回 doc_id 与 status=processing；后台完成后文档 status=active、chunks_count > 0，kb_chunks 有对应记录，Milvus 和 ES 可检索到该文档内容

#### Scenario: 上传 PDF 文档成功

- **WHEN** 管理员上传一个 PDF 文件
- **THEN** 系统先返回 processing；后台提取文本（过滤页眉页脚），分块后向量化写入双索引，文档 status=active

#### Scenario: 上传不支持格式

- **WHEN** 管理员上传一个 .exe 文件
- **THEN** 系统返回错误码 A3001（文档解析失败），HTTP 200，统一响应包 code 不为 "0"；不创建后台入库任务

#### Scenario: 上传制度截图 JPG 成功

- **WHEN** 管理员上传制度流程截图 .jpg，category=regulation
- **THEN** 系统先返回 processing；后台调用 PaddleOCR /ocr 提取文本，经清洗分块向量化写入双索引，最终 doc_id 对应 status=active

#### Scenario: 上传 JPEG 扩展名图片成功

- **WHEN** 管理员上传 .jpeg 扩展名的制度截图，category=regulation
- **THEN** 系统将其识别为 OCR 格式白名单，先返回 processing；后台走与 .jpg 相同入库链路，最终 status=active

#### Scenario: 上传通话录音 MP3 成功

- **WHEN** 管理员上传催收通话录音 .mp3，category=case
- **THEN** 系统先返回 processing；后台调用 ASR Provider 转写文本（静音裁剪 + 分片拼接），经清洗分块向量化写入双索引，最终 status=active

#### Scenario: 上传 Excel 文档成功且来源打标

- **WHEN** 管理员上传一个含多 sheet 的 .xlsx 台账文件，category=script
- **THEN** 系统先返回 processing；后台按 Excel 解析器提取全部 sheet 文本并写入双索引，最终 status=active，kb_documents.source 记录为 "excel"

#### Scenario: 上传 PPT 文档成功且来源打标

- **WHEN** 管理员上传一个 .pptx 培训演示文稿，category=regulation
- **THEN** 系统先返回 processing；后台按 PPT 解析器提取每页文本（含演讲者备注）并写入双索引，最终 status=active，kb_documents.source 记录为 "ppt"

#### Scenario: 上传 AMR 通话录音成功且来源打标

- **WHEN** 管理员上传一个 .amr 电话录音，category=case
- **THEN** 系统先返回 processing；后台经 ffmpeg 解码后走 ASR Provider 转写并写入双索引，最终 status=active，kb_documents.source 记录为 "asr"

#### Scenario: 后台完成后变为 active 可检索

- **GIVEN** 上传已返回 processing
- **WHEN** 后台完成解析、分块与双写
- **THEN** 文档 status 变为 active，kb_chunks 有记录，Milvus 与 ES 可检索到该文档内容

#### Scenario: 新增格式入库失败回滚与 V1 一致

- **WHEN** 管理员上传 .xlsx 文件，解析分块成功但 Embedding 服务熔断
- **THEN** 后台按 V1 既有回滚契约清理已写入的 PG/Milvus/ES，status=failed + A3003（或列表/详情可定位失败），不得长期卡在 processing

### Requirement: 文档状态生命周期管理

系统 MUST 管理文档状态流转：processing（入库中）→ active（可用）或 failed（入库失败）。archived 表示已归档删除。

后台任务失败、job 超时、向量/索引不可用、进程重启导致 worker 丢失等路径 MUST 将 status 置为 `failed`，并保证可通过列表/详情字段 `ingest_error`（及/或既有 error_code）与日志（含 trace_id）定位；MUST NOT 静默长期停留在 `processing`。

processing 期间 MUST 暴露可读进度（见「入库进度可观测」）；完成后进度 MUST 清空或标 `done`；failed 时 MUST 保留 `ingest_error` 直至下次入库/reindex 覆盖。

**processing 不可检索**：`status=processing` 或 `failed` 的文档 MUST NOT 被混合检索/BM25/向量召回命中（仅 `active` 可检索）。

#### Scenario: 入库成功状态流转

- **WHEN** 文档上传并成功完成全链路入库（含后台）
- **THEN** 文档状态从 processing 变为 active

#### Scenario: 入库失败状态流转

- **WHEN** 向量化服务不可用导致入库失败
- **THEN** 文档状态变为 failed，并记录错误日志；不得保持 processing

#### Scenario: 进程异常后不得永久 processing

- **WHEN** 后台入库在 processing 期间失败或超过 job 总时限
- **THEN** status 变为 failed，`ingest_error` 可定位原因；调用方可通过列表/详情识别失败

#### Scenario: 失败原因可在列表或详情读取

- **WHEN** 文档入库失败变为 status=failed
- **THEN** 列表或详情响应含非空 `ingest_error`（或等价 error_code + 短 message），不得仅 status=failed 而无任何可读原因

#### Scenario: processing 文档不可被检索命中

- **GIVEN** 某文档 status=processing，正文 chunks 可能已部分写入
- **WHEN** 用户对相关内容发起混合检索
- **THEN** 该 processing 文档 MUST NOT 出现在召回结果中

### Requirement: 重新解析入库

系统 SHALL 支持对已入库文档执行重新解析（reindex）。

**本变更默认路径 = 仅正文 reindex**（完整重建图/表资产为可选升级，非默认）：若实现无法从原文件完整重抽图/表资产，则 reindex MUST 明确为**仅重建正文索引**：清理并重建正文 chunks/向量后，MUST NOT 在成功响应中暗示图/表资产已恢复；API 与 Gradio MUST 强提示「会清除或暂不恢复图/表资产，完整恢复请重新上传」。若实现可重建资产，则 MUST 先清旧 assets 再按新解析结果写新，与资产存储条款一致。

禁止当前实现中「删除 assets 后只按 `doc.content` 重切却对外表现为完整成功」的静默丢资产行为。

#### Scenario: 重新入库后内容更新

- **WHEN** 管理员对文档执行 reindex 操作
- **THEN** 旧的正文 chunks 和向量被清除，新的正文 chunks 和向量被写入，文档状态变为 active；若为本版仅正文语义，则不得暗示图/表资产已完整恢复

#### Scenario: 仅正文 reindex 时强提示

- **WHEN** 管理员对含内嵌资产的文档执行仅正文语义的 reindex
- **THEN** 正文索引被重建且状态可达 active；响应或 Gradio 明确提示图/表资产未完整恢复/已清除，推荐重新上传

#### Scenario: 可重建资产时先清后写

- **WHEN** 实现支持从原文件/资产完整重建且管理员执行 reindex
- **THEN** 旧 assets 先清理，再按新解析结果重建，不产生旧资产残留且不静默丢多模态能力

### Requirement: 内嵌图片与表格资产存储与删除清理（kb_doc_assets）

系统 MUST 提供内嵌资产（图片/表格）的元数据存储与生命周期管理：

- **存储**：表 `kb_doc_assets`（id / doc_id FK CASCADE / asset_type(image|table) / page / storage_path / ocr_text / mime_type / content_json TEXT（表格行列结构化 JSON，图片为 NULL）/ 公共字段）；
- **图片文件**：落本地磁盘 `KB_ASSETS_DIR`，storage_path 为相对路径，供 Gradio 预览（含上传页/列表侧按 doc 预览，不仅限问答召回）；
- **删除清理**：删除文档时 MUST 同步删除该文档全部 assets 记录并清理本地文件，失败按既有重试与补偿契约；
- **reindex**：与「重新解析入库」本变更最小契约对齐——仅正文 reindex 时 MUST 不强装已重建资产；完整重建路径 MUST 先清旧再写新；
- 新表为增量表，不影响既有 kb_documents/kb_chunks 语义。

#### Scenario: 资产元数据落表与本地存储

- **WHEN** 管理员上传含内嵌图片与表格的文档且抽取开关开启，后台入库完成
- **THEN** kb_doc_assets 有对应记录，图片文件落 KB_ASSETS_DIR，表格 content_json 可供前端渲染

#### Scenario: 删除文档同步清理资产

- **WHEN** 管理员删除已抽取内嵌图片/表格的文档
- **THEN** 该文档 kb_doc_assets 记录全部删除，本地对应文件被清理，kb_documents 状态为 archived

#### Scenario: reindex 重建资产

- **WHEN** 管理员对含内嵌资产的文档执行支持完整重建资产的 reindex
- **THEN** 旧的 assets 记录与本地文件先清理，再按新解析结果重建，不产生旧资产残留

#### Scenario: 仅正文 reindex 不强装已重建资产

- **WHEN** 管理员执行本版仅正文语义的 reindex
- **THEN** 系统不得在成功语义中声称 assets 已完整重建；须通过响应标记或警告表明需重新上传以恢复多模态

#### Scenario: 上传页可按 storage_path 读图

- **WHEN** 文档已抽出 image 资产且 Gradio 与 rag-service 共享 KB_ASSETS_DIR
- **THEN** 上传页/列表侧可按 storage_path 读取并渲染该 doc 的 image，不依赖新建公网静态下载 API

### Requirement: 入库阶段结构化耗时日志

系统 MUST 在入库过程中对至少三个阶段打结构化耗时日志：**解析（parse）**、**正文索引**、**资产索引**（无资产时资产阶段仍记录且耗时可为零或显式跳过）。日志 MUST 能关联 doc_id 与各阶段耗时。

本 Requirement **不修改** `KB_PARSE_TIMEOUT` 仅约束文本提取的含义。端到端后台 job 时限由「入库后台总时限」条款约束，与本日志条款并存。

#### Scenario: 含资产文档记录三阶段耗时

- **GIVEN** 管理员上传将抽出图片或表格的文档，抽取开关已开启
- **WHEN** 后台跑完解析、正文索引与资产索引
- **THEN** 日志分别包含 parse、正文索引、资产索引耗时，并带该文档标识

#### Scenario: 无资产文档仍记录阶段日志

- **GIVEN** 纯文本或图/表开关均关闭
- **WHEN** 入库完成
- **THEN** 仍有 parse 与正文索引耗时日志；资产索引阶段为跳过或耗时为零

#### Scenario: 解析超时语义不变

- **WHEN** 文档解析（文本提取）超过 `KB_PARSE_TIMEOUT`
- **THEN** 仍按既有解析超时控制失败收口；本日志条款不得把解析超时范围扩大到 embedding 或资产索引

## ADDED Requirements

### Requirement: 入库进度可观测

系统 MUST 在文档 `status=processing` 期间暴露可读进度信息：至少包含 `ingest_stage`（取值含 `parse` | `body_index` | `asset_index` | `done`，允许等价命名但语义一致）与可选 `ingest_progress`（0–100 整数，按 stage 离散映射：parse=25、body_index=50、asset_index=75、done=100；无资产时可跳过 asset_index）。列表或详情接口 MUST 可取到上述字段。`ingest_progress` 允许为空（仅 stage 也满足本 Requirement）。完成后 MUST 将 stage 置为 `done` 或清空进度字段。

#### Scenario: processing 时列表可见 stage

- **GIVEN** 文档正在后台入库
- **WHEN** 调用列表或详情
- **THEN** 响应含 status=processing 与当前 ingest_stage（或 progress），调用方可区分卡在哪一阶段

#### Scenario: 完成后进度收口

- **WHEN** 文档变为 active 或 failed
- **THEN** ingest_stage 为 done 或进度字段已清空，不得继续显示过期的中间 stage 为唯一状态

### Requirement: 入库后台总时限与短事务

系统 MUST 为后台入库任务提供总时限配置（如 `KB_INGEST_JOB_TIMEOUT`，默认 15–30 分钟量级）。超时 MUST 将文档置 `failed` 并记录可定位错误，不得无限挂起。

后台入库 MUST 按阶段使用短事务提交，MUST NOT 在整个 parse→资产 OCR→索引期间长时间占用单个 DB Session/连接池连接。

校验通过返回 processing 之前，文档行 MUST 已对列表可见（解决「提示看 processing 但列表没有」）。

#### Scenario: job 超时置 failed

- **WHEN** 后台入库超过配置的总时限仍未完成
- **THEN** 文档 status=failed，列表/详情可识别失败，不得保持 processing

#### Scenario: 早返回后列表立刻可见

- **WHEN** 上传接口已返回 doc_id + processing
- **THEN** 立即查询列表可看到该文档（至少含 doc_id/title/status=processing）

### Requirement: 索引写入孤儿补偿

系统 MUST 在已向 Milvus 与/或 ES 写入该 doc 的索引后，若后续 PG 持久化失败或需回滚文档时，补偿删除该 doc 在 Milvus/ES（及必要的本地资产）中的残留，避免索引孤儿。补偿失败 MUST 记告警日志（含 doc_id/trace_id），不得静默留下可检索脏数据而无失败状态。

#### Scenario: PG 失败后清理向量与 ES

- **WHEN** 正文或资产索引已写入 Milvus/ES 但 PG 提交失败需失败收口
- **THEN** 系统尝试 delete_by_doc（或等价）清理索引；文档最终为 failed 或未留下可检索的成功态脏数据

### Requirement: 对外错误信息脱敏

面向调用方的 API 错误 message MUST NOT 直接拼接裸异常栈或内部服务原始 HTTP 细节；MUST 使用业务可读文案与既有错误码。详细异常 MUST 写入结构化日志并关联 trace_id，便于运维定位。

#### Scenario: 上传失败不回传裸栈

- **WHEN** 后台或同步路径因内部依赖异常失败
- **THEN** 响应 message 为脱敏业务文案 + 错误码；完整异常仅在日志中

### Requirement: 文档详情暴露资产元数据（预览用）

`GET /kb/documents/{doc_id}` MUST 在响应中增加 `assets` 数组（可空），元素至少含 `asset_type`、`page`、`storage_path`；image 类型含 `mime_type` 供 Gradio 读共享 `KB_ASSETS_DIR` 渲染。**不**新增公网静态文件下载端点；仅返回元数据，文件由 Gradio 读共享盘。

#### Scenario: 含图文档详情返回 assets

- **WHEN** 调用详情接口查询已抽出 image 资产的 active 或 processing 文档
- **THEN** 响应 `assets` 含至少一条 `asset_type=image` 且 `storage_path` 非空，Gradio 可据此读盘预览

### Requirement: 后台入库临时文件清理

后台 ingest 使用的临时上传文件（校验通过后持久化供 worker 读取的路径）MUST 在后台任务结束（active 或 failed）的 finally 路径删除，success/failure 均不得长期残留。

#### Scenario: 后台完成后删除临时文件

- **WHEN** 后台 ingest 任务结束（无论 active 或 failed）
- **THEN** 该次入库使用的临时文件已从磁盘删除

### Requirement: processing 僵尸文档回收

系统 MUST 在进程启动时（或等价定期任务）扫描 `status=processing` 且已超过 `KB_INGEST_JOB_TIMEOUT`（自 `update_time` 或创建时间计）的文档，将其置 `failed` 并写入 `ingest_error`（如 worker lost / job timeout），避免进程重启后永久 processing。

#### Scenario: 启动时回收超时 processing

- **GIVEN** 存在因 worker 丢失而长期停留 processing 的文档
- **WHEN** rag-service 启动完成初始化
- **THEN** 该文档被置 failed 且 `ingest_error` 可定位，不再永久 processing

### Requirement: 重复上传后端拒绝

`POST /kb/documents` MUST 拒绝创建第二条「同 title（文件名）且 status=processing」的并行入库：返回明确业务错误（如 B0001），不得静默再开后台任务。Gradio 防重为补充，后端为强制兜底。

#### Scenario: 同名 processing 时 API 拒绝

- **GIVEN** 已存在 title=`report.docx` 且 status=processing 的文档
- **WHEN** 再次 POST 上传同名 `report.docx`
- **THEN** 返回明确业务错误，不创建新 doc_id，不启动第二条后台任务

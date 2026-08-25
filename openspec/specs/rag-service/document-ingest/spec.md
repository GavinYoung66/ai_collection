# RAG Document Ingest Specification

## Purpose

支持催收业务知识文档（PDF/DOCX/MD/TXT/PNG/JPG/JPEG/MP3/WAV/M4A）上传后自动解析、清洗、分块、向量化并写入 Milvus 和 Elasticsearch 双索引，支持单文档增量删除和重新入库，禁止全量重建。

## Requirements

### Requirement: 文档上传与自动入库

系统 SHALL 接受 PDF、DOCX、MD、TXT、PNG、JPG、JPEG、MP3、WAV、M4A 格式文件上传，自动执行：解析提取文本（PDF/DOCX/MD/TXT 本地 Parser；PNG/JPG/JPEG 走 PaddleOCR 独立服务 /ocr；MP3/WAV/M4A 走 ASR Provider 抽象）→ 清洗（去空行、多余空格、页眉页脚、音频静音裁剪）→ 分块 → 向量化（1024dim）→ 写入 Milvus 向量库和 Elasticsearch BM25 索引 → 写入 PostgreSQL kb_chunks 表 → 更新文档状态为 active。格式白名单以外（含 .exe）返回 A3001。

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

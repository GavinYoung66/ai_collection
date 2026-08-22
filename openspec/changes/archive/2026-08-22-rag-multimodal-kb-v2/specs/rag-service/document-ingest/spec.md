## MODIFIED Requirements

### Requirement: 文档上传与自动入库
系统 SHALL 接受 PDF、DOCX、MD、TXT、PNG、JPG、MP3、WAV、M4A 格式文件上传，自动执行：解析提取文本（PDF/DOCX/MD/TXT 本地 Parser；PNG/JPG 走 PaddleOCR 独立服务 /ocr；MP3/WAV/M4A 走 ASR Provider 抽象）→ 清洗（去空行、多余空格、页眉页脚、音频静音裁剪）→ 分块 → 向量化（1024dim）→ 写入 Milvus 向量库和 Elasticsearch BM25 索引 → 写入 PostgreSQL kb_chunks 表 → 更新文档状态为 active。格式白名单以外（含 .exe）返回 A3001。

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

#### Scenario: 上传通话录音 MP3 成功
- **WHEN** 管理员上传催收通话录音 .mp3，category=case
- **THEN** 系统调用 ASR Provider 转写文本（静音裁剪 + 分片拼接），经清洗分块向量化写入双索引，返回 doc_id + status=active

## ADDED Requirements

### Requirement: Embedding 失败可选降级入库（开关默认关闭=V1 行为）
当 `KB_EMBED_DEGRADED_KEEP_TEXT=true`（默认 false=V1 原行为）时，若 batch_embed 调用失败，系统 SHALL 降级为仅写入 ES 文本索引（跳过 Milvus 向量、跳过 kb_chunks.vector_id 为空），文档状态仍为 active；kb_search_logs 对应文档检索时 degraded=true 并溯源。当开关关闭（默认值），行为与 V1 完全一致（Embedding 失败 → 回滚索引 + status=failed + A3003）。

#### Scenario: 开启降级，Embedding 宕机仍可文本检索
- **WHEN** KB_EMBED_DEGRADED_KEEP_TEXT=true，文档解析成功但 Embedding 连续熔断
- **THEN** 系统仅写 ES 索引，不写 Milvus，kb_chunks.vector_id=null，文档 status=active；BM25 检索可命中该文档，但 Milvus 向量召回不可用（ degraded=true ）

#### Scenario: 开关关闭（默认）= V1 失败路径
- **WHEN** KB_EMBED_DEGRADED_KEEP_TEXT=false，Embedding 失败
- **THEN** 系统按 V1 既有回滚契约：清理 PG/ES/Milvus 已写入，status=failed + A3003，与 V1 现有行为完全一致

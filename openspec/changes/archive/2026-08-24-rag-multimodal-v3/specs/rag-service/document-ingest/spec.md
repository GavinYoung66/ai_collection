# RAG Document Ingest Specification (Delta)

> **禁止触碰范围（V1/V2 已实现能力）**：本变更仅修复 V1/V2 遗留问题。严禁修改、重构、破坏 V1/V2 已落地业务代码、接口、能力与配置；V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10）保持原样；所有新增能力均以开关默认 false 接入，关闭时行为与 V2 完全一致；禁止引入遗留清单之外的新功能、禁止新增错误码、禁止修改对外接口路径与统一响应包 `{"code":"0",message,data,trace_id}`。

## MODIFIED Requirements

### Requirement: 文档上传与自动入库

系统 SHALL 接受 PDF、DOCX、MD、TXT、PNG、JPG、**JPEG**、MP3、WAV、M4A 格式文件上传，自动执行：解析提取文本（PDF/DOCX/MD/TXT 本地 Parser；PNG/JPG/JPEG 走 PaddleOCR 独立服务 /ocr；MP3/WAV/M4A 走 ASR Provider 抽象）→ 清洗（去空行、多余空格、页眉页脚、音频静音裁剪）→ 分块 → 向量化（1024dim）→ 写入 Milvus 向量库和 Elasticsearch BM25 索引 → 写入 PostgreSQL kb_chunks 表 → 更新文档状态为 active。格式白名单以外（含 .exe）返回 A3001。

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

## ADDED Requirements

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

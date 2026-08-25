# RAG Multimodal Ingest Specification (Delta)

> **禁止触碰范围（V1/V2 已实现能力）**：本变更仅修复 V1/V2 遗留问题。严禁修改、重构、破坏 V1/V2 已落地业务代码、接口、能力与配置；V2 已确认核心业务逻辑（Embedding ES-only 降级、alembic 迁移 0004/0005/0006、冒烟 Step10）保持原样；所有新增能力均以开关默认 false 接入，关闭时行为与 V2 完全一致；禁止引入遗留清单之外的新功能、禁止新增错误码、禁止修改对外接口路径与统一响应包 `{"code":"0",message,data,trace_id}`。

## MODIFIED Requirements

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

系统 SHALL 支持 .mp3 / .wav / .m4a 格式音频上传，通过 ASR Provider 抽象（DashScope Paraformer 默认，Whisper 可切换）执行语音转文字；**MUST 在转写前执行 VAD 分片**：对整段音频执行静音裁剪（去除首尾静音段），并按 ≤ 60s 时长切分为多个分片，逐片转写后按时间顺序拼接结果；**规格澄清（审计 D8）**："通话开场白"裁剪为规格超前表述，V3 未实现（静音裁剪已按 RMS 阈值实现，开场白识别需语义级 VAD，留待后续迭代）；提取失败 A3001，超时 60s、3 次重试、熔断 `rag:asr:degraded`。ASR Provider 抽象 MUST 支持至少两个可切换实现：DashScope Paraformer（默认）与 Whisper（本地），Whisper 实现 MUST 可完成 WAV/MP3 转写并返回与 DashScope 一致的转写文本契约，切换 `ASR_PROVIDER` 配置不影响对外请求/响应契约。

#### Scenario: 通话录音转写入库成功

- **WHEN** 管理员上传一段 90s 催收通话 MP3 录音，category=case
- **THEN** 系统按 60s 分片 → 逐片去静音转写 → 拼接转写文本，分块写入双索引，文档 status=active，kb_documents.source 字段记录为 "asr"

#### Scenario: 90s 录音 VAD 分片后逐片转写

- **WHEN** 管理员上传 90s 通话 WAV 文件
- **THEN** 系统将音频裁剪静音后切分为至少 2 个 ≤60s 分片，逐片调用 Provider 转写，最终入库文本为各分片转写结果按时间顺序拼接的完整内容，且不丢失任一分片文本

#### Scenario: 短静音音频单分片转写

- **WHEN** 管理员上传 20s 无静音 WAV 文件
- **THEN** VAD 检测无静音段、总时长 ≤60s，系统以单个分片直接转写，不强制切分，结果与整段转写一致

#### Scenario: ASR Provider 切换不影响入参

- **WHEN** 管理员在配置中切换 ASR_PROVIDER=whisper 后上传 WAV 文件
- **THEN** 请求/响应契约无变化，系统内部调用新 Provider，转写结果仍按 V1 既有清洗规则去空行与脏数据

#### Scenario: ASR Provider 切换为 Whisper 后转写正常

- **WHEN** 管理员在配置中切换 ASR_PROVIDER=whisper 后上传 WAV 文件，Whisper 模型可用
- **THEN** 请求/响应契约无变化，系统内部调用 Whisper Provider 完成转写，转写结果仍按 V1/V2 既有清洗规则去空行与脏数据

#### Scenario: Whisper Provider 无 Key 或模型不可用时降级

- **WHEN** ASR_PROVIDER=whisper 但本地模型加载失败或转写异常
- **THEN** 系统按既有 ASR 异常路径返回 A3001（或降级开关开启时的占位入库），不抛未捕获异常，不影响服务其他接口

#### Scenario: 损坏音频解析失败

- **WHEN** 管理员上传损坏加密 M4A 文件
- **THEN** ASR 调用抛出异常，系统返回 A3001，status=failed，不写入任何索引

### Requirement: OCR/ASR 解析失败降级（新增）

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

# RAG Multimodal Ingest Specification

## Purpose

为催收业务知识的非文本载体提供多模态解析入库能力：图片经 OCR 独立服务识别文字、音频经 ASR Provider 转写文字，两者产出的纯文本无缝接入 V1 既有的清洗、分块、向量化、双索引链路，复用 IngestService 状态机与回滚契约。

## Requirements

### Requirement: 图片 OCR 解析支持
系统 SHALL 支持 .png / .jpg 格式图片上传，通过 PaddleOCR 独立服务（endpoint `/ocr`）提取图片文字；识别失败时走统一 A3001 文档解析失败分支。OCR 调用携带 30s 超时、3 次指数退避、3 连失败熔断标识 `rag:ocr:degraded`（TTL 60s）。

#### Scenario: 制度截图 OCR 成功入库
- **WHEN** 管理员上传一张包含"逾期协商流程"的 JPG 制度截图，category=regulation
- **THEN** OCR 服务返回文字提取结果，经清洗分块写入双索引，文档 status=active，后续以该图文字检索可命中对应 chunk

#### Scenario: OCR 服务熔断降级
- **WHEN** PaddleOCR 连续 3 次失败后，管理员再上传 PNG 文件
- **THEN** 系统直接短路调用，返回 A3001，status=failed，并在 60s 内所有图片上传均走熔断短路径

#### Scenario: 纯空白图无文字
- **WHEN** 管理员上传一张纯白无文字 PNG 文件
- **THEN** OCR 返回空文本，分块服务产生 0 有效 chunk，走 A3002 文本切片失败，status=failed

### Requirement: 音频 ASR 转写支持
系统 SHALL 支持 .mp3 / .wav / .m4a 格式音频上传，通过 ASR Provider 抽象（DashScope Paraformer 默认，Whisper 可切换）执行语音转文字；前置裁剪静音段和通话开场白，每段 ≤ 60s 分片转写，结果拼接；提取失败 A3001，超时 60s、3 次重试、熔断 `rag:asr:degraded`。

#### Scenario: 通话录音转写入库成功
- **WHEN** 管理员上传一段 90s 催收通话 MP3 录音，category=case
- **THEN** 系统按 60s 分片 → 去静音 → 拼接转写文本，分块写入双索引，文档 status=active，kb_documents.source 字段记录为 "asr:dashscope"

#### Scenario: ASR Provider 切换不影响入参
- **WHEN** 管理员在配置中切换 ASR_PROVIDER=whisper 后上传 WAV 文件
- **THEN** 请求/响应契约无变化，系统内部调用新 Provider，转写结果仍按 V1 既有清洗规则去空行与脏数据

#### Scenario: 损坏音频解析失败
- **WHEN** 管理员上传损坏加密 M4A 文件
- **THEN** ASR 调用抛出异常，系统返回 A3001，status=failed，不写入任何索引

### Requirement: 外部解析器状态机与回滚复用
OCR / ASR 解析产出纯文本后，系统 SHALL/MUST 复用 V1 IngestService 的后续 7 步流水线（clean → processing 占位 → split → batch_embed → Milvus+ES 双写 → kb_chunks 持久化 → status=active）；多存储回滚契约与 V1 完全一致（PG 成功但索引失败 → MUST 回滚索引 + status=failed + 返回 A3003）。

#### Scenario: OCR 文本成功但 Embedding 不可用
- **WHEN** PNG OCR 提取 1000 字，但 Embedding 熔断返回异常
- **THEN** IngestService 按 V1 既有流程 rollback：不写 Milvus/ES/kb_chunks，文档 status=failed，返回 A3003，行为与 V1 DOCX 解析成功但 Embedding 失败时完全一致

#### Scenario: ASR 转写成功但 Milvus 半写失败
- **WHEN** MP3 转写成功、分块、批量 Milvus 写入一半崩溃
- **THEN** Milvus delete_by_doc 清理已写入向量 + ES 回滚 + kb_chunks 清理，文档 status=failed，禁止 PG=active 但索引脏数据状态

## ADDED Requirements

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

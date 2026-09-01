## Why

V5 多模态入库已落地，但 Word/含表文档在运行时暴露多处问题：图/表开关未独立生效、资产 chunk 逐条 embed 导致入库可达数分钟、DOCX 浮动图抽不出、ingest 缺少阶段耗时日志、Gradio 把读超时报成后端宕机；冒烟又发现单张 OCR 失败时对共享 Session 全量 `rollback()`，把未提交的 `kb_documents` 一并滚掉，后续批量写表格 `kb_chunks` 触发 FK（doc_id 不存在）整单 B0007。这些缺陷破坏「上传 → 入库完成」的可用性，需要在不改 POST 契约、不重写归档 V5 的前提下做增量修复。

## What Changes

- **图/表抽取独立门控**：`KB_IMAGE_EXTRACT_ENABLED` 与 `KB_TABLE_EXTRACT_ENABLED` MUST 分别决定是否产生对应资产与结构化 chunk；只开图不得抽表，只开表不得抽图。覆盖实现中「任一开关开启即抽取全部资产」的错误行为。
- **资产 chunk 批量写入**：同一文档/同一张表的资产文本 chunk 写入 MUST 走 `batch_embed` 与批量双写；禁止每行一次完整索引 RPC。检索语义不变：表格仍「每行一个 chunk」。
- **单资产失败与 Session 安全**：单张 OCR / 单表准备失败 MUST 仅 skip + warning；MUST NOT 对共享 DB Session 做全量 `rollback()`。进入资产阶段时 `kb_documents` 行 MUST 已可被 FK 引用（正文阶段后显式 commit，或等价保证）。批量 embedding/双写失败仍按既有契约回滚索引并 A3003（不削弱）。不起 OCR 服务作为本变更前置。
- **DOCX 浮动/粘贴图抽取**：抽图开关开启时，DOCX 的浮动图、粘贴图（含 `wp:anchor` / `word/media`）MUST 抽出；与 `inline_shapes` 按内容去重。工作区已有 media 补漏实现，本变更将其固化为契约，Apply 时对齐/补测、禁止回退。
- **ingest 阶段耗时日志**：parse / 正文索引 / 资产索引 MUST 打结构化耗时日志。不改 `POST /kb/documents` 契约，不加端到端 SLA，不改 `KB_PARSE_TIMEOUT` 语义。
- **Gradio 上传超时文案拆分**：连接失败 →「后端服务暂不可用」；读超时且请求已发出 →「入库处理中」（刷列表看 processing/active，勿重传）。工作区已有 `_UPLOAD_TIMEOUT=600` 与 ReadTimeout 分支，本变更只修正 spec Scenario，不再扩 UI、不再改超时秒数；**禁止回退**该文案。
- **非本变更**：异步 202 / 先返回 processing、端到端 ingest SLA、改表结构、改 JWT、改「一行一 chunk」检索粒度、上传页 docx 预览、重复上传防护、进度字段、强制部署 ocr-service。

## Capabilities

### New Capabilities

无。（增量修正既有能力，不新增 capability。）

### Modified Capabilities

- `rag-service/multimodal-ingest`: 图/表开关独立门控；资产 chunk 批量写入；单资产失败不得全量 rollback 共享 Session；DOCX 浮动/粘贴图在抽图开启时必须抽出。
- `rag-service/document-ingest`: 仅增加 ingest 阶段结构化耗时日志；不改变解析超时与同步入库契约。
- `rag-service/gradio-frontend`: 拆分上传「连接失败」与「读超时」的提示语义。

## Impact

- **代码（Apply 阶段，本 propose 不改）**：`services/rag-service/app/parsers/asset_extractor.py`（开关分流、DOCX media 补漏）；`services/rag-service/app/services/asset_service.py`（批量索引、单资产失败 Session 安全）；`services/rag-service/app/services/ingest_service.py`（阶段日志、按类型处理资产、正文后 commit 边界若需要）；`services/rag-service/gradio/app.py`（核对既有 ReadTimeout 文案，禁止回退）；相关单测。
- **配置**：不新增环境变量；继续使用既有 `KB_IMAGE_EXTRACT_ENABLED` / `KB_TABLE_EXTRACT_ENABLED`。
- **接口契约**：`POST /kb/documents` 仍为同步等完全程后返回；入参/出参/错误码不变。
- **兼容性**：禁止修改 `openspec/changes/archive/**`（含归档 V5 与 `2026-08-31-fix-rag-parse-error1`）；禁止改主 spec（archive 时再 sync）；工作区未提交的 `.env` / `data/assets` 不纳入本变更。

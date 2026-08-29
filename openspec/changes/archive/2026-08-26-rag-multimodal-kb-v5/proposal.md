# RAG 多模态知识库 V5 — 多模态混合检索增强 + Gradio 前端 + 在线大模型回答（rag-multimodal-kb-v5）

## Why

RAG 多模态知识库 V1~V4 已交付并归档（基础检索、多模态入库、智能检索管道、摘要元数据全部闭环，测试基线 240 passed / 1 skipped），但面向真实催收业务仍存在四类未满足的诉求：

1. **真实数据源缺口**：催收制度/流程/台账大量以 Excel 表格、PPT 演示存在（无法入库检索）；话务平台电话录音除 MP3/WAV/M4A 外普遍存在 **AMR/WMA** 格式（当前白名单外，直接 A3001）；
2. **多模态检索质量缺口**：PDF/DOCX/PPT 内嵌的图片（制度截图、流程图）与表格无法被文本检索命中（"文搜图"缺失），表格被文本化后行列结构被打散，任意单元格命中无从谈起；
3. **使用门槛缺口**：RAG 仅提供 REST API，业务方无法在浏览器中上传文档、可视化"问题 → 召回片段 → 回答"全链路来验证知识库质量；
4. **消费方式缺口**：检索仅返回片段，调用方需自行拼接回答；V5 以开关门控接入在线大模型生成**带溯源引用的回答**（默认关闭，关闭时行为与 V4 完全一致，零回归）。

## What Changes

**新增能力 1 — Gradio 浏览器前端入口（`gradio-frontend`，独立 compose 服务）**：

- 上传页（全部支持格式 txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma）+ 问答页（问题 → 召回 chunk（含图片/表格预览）→ LLM 回答全链路展示）；
- 召回片段展示溯源元数据（doc_id、来源 source、页码等）；后端宕机/LLM 熔断等异常友好提示，不白屏、不 500；
- 复用既有 `/kb/*` API，**不修改任何后端接口契约**。

**新增能力 2 — RAG 检索接入在线大模型（`llm-answer`，开关门控）**：

- 新增开关 `KB_LLM_ANSWER_ENABLED`（默认 false）：开启且请求显式 `answer=true` 时，检索后经 LLM Provider 生成带溯源引用的回答（`answer` 字段）；关闭时响应与 V4 完全一致，**零 LLM 调用**；
- LLM Provider 抽象（在线 DashScope 默认 / 本地 OpenAI 兼容），切换不影响对外请求/响应契约；
- LLM 超时（`LLM_TIMEOUT=5s`）/熔断/不可用 → 降级为纯检索结果（`answer=null + degraded=true`），**不抛错、不 500**；
- **§1.4「RAG-Service 不接 LLM」边界显式放开**：仅当开关开启时 rag-service 才向外调用 LLM，属受控例外；`include_answer` 类字段不回归。

**新增能力 3 — 文档格式扩展（`.xlsx`/`.pptx`，修改 `document-ingest`）**：

- `.xlsx`：全部 sheet 入库、首行=表头、行切块（每行序列化 `列名1:值1, 列名2:值2,...`）、sheet 名作前缀冗余进每行 chunk；空行/全空值行跳过；
- `.pptx`：每页抽取 标题+正文(文本框)+演讲者备注（备注并入页文本），过滤页码/版式占位文字（启发式，保守防误删正文）；
- 新增格式复用 V1 七步流水线（clean→split→embed→Milvus+ES 双写→kb_chunks 持久化→active），source 打标区分（`excel`/`ppt`），回滚契约与 V1 一致（PG 成功但索引失败 → 回滚 + status=failed + A3003）；
- 既有格式（txt/md/pdf/docx/png/jpg/jpeg/mp3/wav/m4a）解析行为与 V4 完全一致，存量测试零回归。

**新增能力 4 — 内嵌图片与表格解析（修改 `multimodal-ingest`）**：

- PDF/DOCX/PPTX 内嵌图片抽取（PNG/JPEG、位图、矢量图经 PyMuPDF/Pillow 渲染为位图）→ **OCR 文本索引入库（文搜图）**（复用 ocr-service:8200，source=`asset_image`）+ 图片文件落**本地磁盘** `data/assets/` + 元数据落新表 `kb_doc_assets`（含文档、页码）；
- 内嵌表格完整解析：保留行列结构不打散，问答可命中任意单元格；表格/图片随检索结果返回 `results[].assets` 预览元数据；
- 开关门控：`KB_IMAGE_EXTRACT_ENABLED` / `KB_TABLE_EXTRACT_ENABLED`（默认 false），关闭=仅文本、V4 行为。

**新增能力 5 — 音频格式扩展（AMR/WMA，修改 `multimodal-ingest`）**：

- `.amr`/`.wma` 经 ffmpeg 解码（服务端二进制依赖）后走既有 ASR Provider 抽象（DashScope 默认 / Whisper 可切），VAD/分片策略对齐既有实现（解码为 WAV 后统一走 VAD 管线）；兼容 MP3/WAV/M4A；
- 损坏音频返回业务错误（A3001 或降级开关占位入库），worker 不卡死、不崩溃、不影响后续请求；
- ASR Provider 抽象契约不变，切换 `ASR_PROVIDER` 不影响对外请求/响应。

**明确不做（Non-Goals，标记后续迭代 Backlog）**：

- 图片向量检索（CLIP 类，需新 embedding 服务）、扫描件 PDF 整本 OCR、知识图谱；
- 治理能力：LangSmith、评估模块、文档版本管理+灰度、Prompt 动态配置、敏感词 CRUD；
- LLM 回答不做流式（SSE），为同步可选字段；不做对话式追问式回答（仅单轮回答）；
- 不修改 V1~V4 既有接口路径/响应包/错误码（**禁止新增错误码**）；不改既有表列语义（只加列/加表）。

## Capabilities

### New Capabilities

- `rag-service/gradio-frontend`: Gradio 浏览器前端入口——上传页/问答页全链路可视化、图片与表格预览、溯源元数据展示、异常友好提示；独立 compose 服务，复用既有 `/kb/*` API，不动后端契约。
- `rag-service/llm-answer`: 检索接入在线大模型回答——`KB_LLM_ANSWER_ENABLED` 开关门控、LLM Provider 抽象（在线/本地）、超时/熔断降级纯检索、`answer` 可选响应字段（带溯源引用）。

### Modified Capabilities

- `rag-service/document-ingest`: 格式白名单扩展 `.xlsx/.pptx/.amr/.wma`（source 打标 excel/ppt/asr）；入库链路对新增格式复用 V1 七步流水线与回滚契约；`kb_doc_assets` 新表（内嵌图片/表格元数据 + 本地存储定位）；文档删除时同步清理 assets。
- `rag-service/multimodal-ingest`: 内嵌图片抽取（OCR 文本索引文搜图 + 本地存储 + 元数据落表）；内嵌表格结构化解析（保留行列结构，不打散）；AMR/WMA 音频解码接入 ASR 抽象与损坏音频容错。
- `rag-service/hybrid-search`: 检索响应新增可选字段 `answer`（LLM 开关开启且请求显式开启时填充，否则恒 null）与 `results[].assets`（命中 chunk 关联的图片/表格预览元数据，缺省为空）；图片 OCR 文本 chunk / 表格 chunk 参与既有召回、RRF、Rerank、双闸门链路，不改变既有检索契约。

## Impact

- **代码**（`services/rag-service/app/`）：`parsers/`（新增 `xlsx_parser.py`、`pptx_parser.py`、`asset_extractor.py`；`base.py` Parser 抽象扩展 assets 返回）、`services/`（新增 `llm_service.py`、`llm_providers/`、`asset_service.py`、`audio_decoder.py`；`ingest_service.py` 扩展新格式与 assets 入库、`search_service.py` 响应扩展）、`api/`（`documents.py` 白名单、`search.py` answer/assets 组装）、`models/`（`orm.py` kb_doc_assets、`search.py` answer/assets 字段）、`vectorstore/` 不变（复用既有双索引）；`services/rag-service/gradio/`（新增 Gradio 应用）。
- **数据库**：Alembic 增量迁移新增 `kb_doc_assets` 表（id/doc_id FK CASCADE/asset_type(image|table)/page/storage_path/ocr_text/mime_type/公共字段），版本号接续既有最大 revision；既有表零改动。
- **存储**：`services/rag-service/data/assets/` 本地磁盘目录（Docker 挂载 volume，供 Gradio 预览与删除清理）；ES/Milvus 不新增字段、不重建索引（图片 OCR 文本以普通 chunk 入库，表格以序列化文本 chunk 入库）。
- **API**（完整对照见 design.md）：`POST /kb/search` 请求新增可选 `answer?: bool`（缺省 false）、响应新增 `answer`（可空）与 `results[].assets`（可空，缺省为空）；`POST /kb/documents` 上传白名单扩展 4 种新格式；其余接口（列表/详情/删除/reindex/sensitive-words/health）零改动。
- **配置**（`common/config/settings.py`，供全部 7 服务共用）：`KB_LLM_ANSWER_ENABLED`(false)、`LLM_PROVIDER`(dashscope)、`LLM_TIMEOUT`(5s)、`KB_IMAGE_EXTRACT_ENABLED`(false)、`KB_TABLE_EXTRACT_ENABLED`(false)、`KB_EXCEL_SHEET_ALL`(true)、`KB_EXCEL_HEADER_ROW`(1)、`KB_ASSETS_DIR`、`KB_ASR_AMR_WMA_ENABLED`(false)；Dockerfile ENV / `.env.example` 同步。
- **部署**：docker-compose（base/dev）新增 `gradio` 服务（port 7860，依赖 rag-service healthy）+ `rag-service` 挂载 assets 数据卷；rag-service Dockerfile 增装 ffmpeg、`COPY gradio/`、新开关 ENV。
- **测试**：新增单元测试（xlsx/pptx 解析、asset 抽取、AMR/WMA 解码、LLM 开关/降级、search 响应扩展）；存量 240 passed 基线不回退；`smoke_rag_v5.sh` 全链路冒烟（含 Gradio 页面检查）。
- **风险与回滚**：新格式/多模态/LLM 全部以开关引入（默认 false），关闭即 V4 行为，可一键回退；`kb_doc_assets` 为新增表、assets 为新增目录，不影响存量；图片抽取失败按既有 A3001 路径；LLM 不确定性与出网以开关默认关 + 超时/熔断降级收敛；Gradio 仅内部使用，不接公网。

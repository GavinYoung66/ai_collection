# RAG 多模态知识库 V5 — 多模态混合检索增强 + Gradio 前端 + 在线大模型回答：技术设计

## Context

V1~V4 已归档：9 个主 spec 在 `openspec/specs/rag-service/`，测试基线 240 passed / 1 skipped，检索主链路（双路召回 → RRF → Rerank → 双闸门 → 行级权限 → 日志/缓存）稳定。V5 动机与范围见 proposal.md；本设计只解释"怎么做"。

存量约束（设计必须遵守）：

- 分层单向依赖（Controller → Service → Repository），错误码全集 84 条**禁新增**，统一响应包 `{"code":"0",message,data,trace_id}`，测试门禁 ≥85%、合规红线 100%。
- **PRD §1.4 边界**：V1~V4 RAG-Service 只提供被动检索、不向外调用 LLM。V5 以 `KB_LLM_ANSWER_ENABLED`（默认 false）**显式放开受控例外**——仅开关开启且请求显式 `answer=true` 且未触发兜底时调用 LLM；其余路径零 LLM 调用，`include_answer` 类字段不回归。
- 增量开发：不修改 V1~V4 对外接口路径/响应包/错误码、不改既有表列语义（只加表）、不改 Milvus/ES schema（不新增字段、不重建索引）、不改既有业务主逻辑；新能力全部以开关接入，默认 false。
- 本期用户已定案：①图片文件存储=**本地磁盘**（`data/assets/`，Docker 挂载 volume）；②Gradio=**独立 compose 服务**（端口 7860）。

## Goals / Non-Goals

**Goals:**

- 新增 `.xlsx`/`.pptx` 解析（复用 V1 七步流水线 + source 打标 excel/ppt），存量格式零回归；
- PDF/DOCX/PPTX 内嵌图片抽取 → OCR 文本索引（**文搜图**）+ 本地存储 + `kb_doc_assets` 元数据；内嵌表格结构化解析（保留行列结构，问答命中任意单元格）；
- 新增 `.amr`/`.wma` 音频（ffmpeg 解码 → 复用 VAD/ASR 管线），损坏音频业务错误不卡 worker；
- Gradio 浏览器前端（独立 compose 服务）复用 `/kb/*` API，全链路可视化 + 异常友好提示；
- LLM 回答（开关门控 + Provider 抽象 + 超时/熔断降级纯检索），响应新增 `answer`/`results[].assets` 可选字段；
- 全量向后兼容：新增开关默认 false，关闭 = V4 行为；存量调用方零改造。

**Non-Goals:**

- 图片向量检索（CLIP 类，需新 embedding 服务）、扫描件整本 OCR、知识图谱——Backlog；
- Milvus/ES 不新增字段、不重建索引；kb_chunks/kb_search_logs 表结构不变；
- LLM 回答不做流式（SSE）、不做多轮追问式回答（仅单轮）；
- 不新增错误码；不改既有接口路径/请求/响应既有字段语义；不做治理能力（LangSmith/评估/版本灰度/敏感词 CRUD）。

## Decisions

### D1. 新增格式复用 V1 七步流水线，不新建入库链路

- **决议**：`.xlsx`/`.pptx`/`.amr`/`.wma` 一律走既有 `IngestService` 七步流水线（clean → processing 占位 → split → batch_embed → Milvus+ES 双写 → kb_chunks 持久化 → active），仅扩展 ParserFactory 分发与格式白名单；`kb_documents.source` 打标：`excel`/`ppt`/`asr`（新增格式在 chunk 写入时透传 source）。
- **关键 Diff 要点**（`app/parsers/base.py` / `app/services/ingest_service.py`）：
  - `DocumentParser` 抽象扩展一个可选 hook：`async extract_text()` 保持既有签名不变；新格式解析器返回 `(text, assets)` 的扩展由**新方法** `extract_with_assets()` 承担（默认降级调用 `extract_text()` 返回 `(text, [])`），既有 6 个 Parser 零改动；
  - `ParserFactory` 注册表新增 `.xlsx→XlsxParser`、`.pptx→PptxParser`，`.amr/.wma→AudioParser`（复用既有 audio 分支）；白名单 `SUPPORTED_EXTENSIONS` 常量扩展 4 个后缀；
  - `ingest_service.py` 在 `status=active` 前，若开关 `KB_IMAGE_EXTRACT_ENABLED`/`KB_TABLE_EXTRACT_ENABLED` 开启且解析器返回 assets，则调用 `asset_service` 落 `kb_doc_assets` + 写本地文件（正文 chunk 已入库后再落资产，资产失败仅 warning 不回滚正文）。
- **备选（放弃）**：为 xlsx/pptx 新建独立入库编排 → 与既有回滚契约、降级矩阵、日志链路重复实现，收益为零。

### D2. Excel 解析：openpyxl、全部 sheet、行切块、sheet 名前缀

- **决议**：`app/parsers/xlsx_parser.py`（openpyxl，只读模式）：
  - 全部 sheet 遍历（`KB_EXCEL_SHEET_ALL=true` 默认；false 仅首个）；表头行 = `KB_EXCEL_HEADER_ROW`（默认 1）；
  - 每行序列化 `f"{sheet名}: 列名1:值1, 列名2:值2,..."`（sheet 名前缀冗余进每行 chunk）；空行/全空值行跳过；
  - 文本超长行由既有 chunk_service 二次切分（overlap=50，len<10 过滤）；
  - 解析异常/加密/空 sheet → 沿既有失败路径（A3001 / A3002）。
- **备选（放弃）**：整表合并为一个超大 chunk → 单元格级命中率低、检索精度差；按 sheet 分块而非按行 → 无法命中具体行。

### D3. PPT 解析：python-pptx、标题+正文+备注、保守占位过滤

- **决议**：`app/parsers/pptx_parser.py`（python-pptx）：
  - 逐页抽取：slide 标题（title placeholder）+ 正文（非 title 文本框）+ 演讲者备注（`slide.notes_slide.notes_text_frame`，并入页文本）；
  - 过滤：页脚/页码（形如 `^\s*\d+\s*$` 或 `/总页数` 模式）与版式占位文案（"点击添加标题/副标题/在此处输入"等已知文案表），**保守启发式**：仅删确定性的页码行与精确匹配的占位文案，正文含数字/短句不删；
  - 多页按顺序拼接，页间插换行，走既有分块链路。
- **备选（放弃）**：逐页独立成 chunk → 页内容过短、语义稀疏；引入版面分析模型 → 超范围且依赖重。

### D4. 内嵌图片抽取与文搜图：OCR 文本索引（轻量），非 CLIP 向量

- **决议**（对齐用户提示与 PRD §10.2 V5 技术取舍）：`app/parsers/asset_extractor.py` 统一负责 PDF/DOCX/PPTX 内嵌图片抽取：
  - **抽取**：PDF 用 PyMuPDF（fitz）`page.get_images` + 位图直接落盘、矢量图经 `page.get_drawings`/渲染接口转为位图；DOCX 用 python-docx `document.inline_shapes` 取 `blip` 二进制；PPTX 复用 D3 已解析的图片部件；
  - **渲染**：矢量图经 Pillow 渲染为 PNG 位图（尺寸上限 `KB_ASSETS_MAX_SIZE` 默认 1024px，等比缩放）后落盘；
  - **文搜图**：每张图片调 ocr-service:8200（既有 `ocr_client.py`，30s 超时/3 次退避/熔断 `rag:ocr:degraded`）识别文字，OCR 文本以**普通 chunk** 走既有双索引入库（source=`asset_image`，chunk 前缀 `[图片] 页码P{n}: `），实现文本索引的文搜图——**图片向量（CLIP）为 Backlog**；
  - **元数据**：`kb_doc_assets` 落一条 `asset_type=image`（page、storage*path 相对路径、ocr_text、mime_type）；图片文件落 `KB_ASSETS_DIR`（默认 `data/assets/{doc_id}/{page}*{idx}.png`）；
  - **容错**：单张抽取/OCR 失败仅 warning（含 trace_id）+ 跳过该资产，不阻断正文入库；开关 `KB_IMAGE_EXTRACT_ENABLED=false` 时整段逻辑短路（零开销）。
- **备选（放弃）**：CLIP 图片向量（需新 embedding 服务 + Milvus 新 collection + 全链路改造）→ V5 先用 OCR 文本索引 + 图片存储供预览，满足验收"文搜图命中 + 返回图片预览"；CLIP 入 Backlog。
- **关键 Diff 要点**（`app/services/ocr_client.py` 复用；`app/services/ingest_service.py` 接入）：
  - `asset_extractor` 只做抽取与 OCR，不碰正文解析逻辑；正文解析结果与 assets 并列返回给 ingest 编排。

### D5. 内嵌表格结构化解析：保留行列结构，不打散

- **决议**：`app/parsers/asset_extractor.py` 内嵌表格抽取：
  - PDF 用 pdfplumber `page.extract_tables()`；DOCX 用 python-docx `doc.tables`；PPTX 用 python-pptx table shape；
  - 每行序列化 `f"表格(第{page}页): 列名1:值1, 列名2:值2,..."` 作为 chunk 入库（保留行列结构、不打散成零散文本）；合并单元格取左上角值 + 行内保留（启发式）；
  - `kb_doc_assets` 落 `asset_type=table`（page、storage_path 指向序列化预览源、ocr_text=序列化文本、mime_type、`content_json` 行列结构化 JSON——表格预览渲染单一数据源，Gradio gr.Dataframe 直接消费，不依赖文本还原）；
  - 检索命中该 chunk 时，`results[].assets` 返回表格预览元数据，Gradio 渲染表格视图；
  - 开关 `KB_TABLE_EXTRACT_ENABLED=false` 时走 V4 文本化路径（表格文本混入正文）。
- **备选（放弃）**：表格整表单 chunk → 大表超 chunk 上限、命中单元行时整表返回过重；向量级表格检索 → Backlog。

### D6. AMR/WMA 音频：ffmpeg 解码 → 复用既有 VAD/ASR 管线

- **决议**（对齐用户提示"Client 解码 vs Provider 侧"）：`app/services/audio_decoder.py` 新增，**Client 侧解码**：ffmpeg（subprocess/pydub 封装）将 AMR/WMA 解码为标准 WAV → 交给既有 `audio_parser.py` VAD 静音裁剪 + ≤60s 分片 + `asr_client.py` Provider 转写；**ASR Provider 抽象契约零改动**（DashScope 默认 / Whisper 可切，切换不影响对外契约）。
  - 开关 `KB_ASR_AMR_WMA_ENABLED`（默认 false）门控；ffmpeg 不存在/解码失败 → 业务错误 A3001（或 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 降级占位），**异常捕获保证 worker 不卡死**（解码放独立 try/except + 超时控制，不污染进程级 asyncio 事件循环）；
  - 解码产物 WAV 用后即删（临时目录），不残留。
- **备选（放弃）**：Provider 侧解码（DashScope/Whisper 原生支持有限格式）→ AMR/WMA 兼容性依赖供应商，且"切换 ASR_PROVIDER 不影响契约"约束会被破坏；pydub 单独解码 → 底层仍依赖 ffmpeg，直接封装 ffmpeg 更轻。

### D7. kb_doc_assets 表 + 生命周期清理

- **决议**：Alembic 迁移 `0008_kb_doc_assets.py`（**版本号 = 既有最大 revision 0007 的下一个**，不硬编码）：
  - 新表 `kb_doc_assets`：`id BIGSERIAL PK`、`doc_id UUID FK kb_documents.id ON DELETE CASCADE`、`asset_type VARCHAR(10) + CHECK (asset_type IN ('image','table'))`、`page INT`、`storage_path VARCHAR(500)`、`ocr_text TEXT`、`mime_type VARCHAR(100)`、`content_json TEXT`（表格行列结构化 JSON，图片为 NULL，表格预览渲染单一数据源）、公共字段 `create_time/update_time/is_delete`；索引：`(doc_id)`、`(doc_id, asset_type)`。
  - `app/models/orm.py` 新增 `KbDocAsset` ORM（与 DDL 对齐，含 docstring）。
  - **删除清理**：`doc_repo.py` 删除文档时，`asset_repo.py` 级联删 `kb_doc_assets` 记录 + `asset_service.py` 清理 `KB_ASSETS_DIR/{doc_id}/` 目录；失败按既有"删除失败重试与补偿"契约（重试 2 次 + 告警标记待补偿）。
  - **reindex**：先清旧 assets（记录 + 本地目录）再重建。
- **备选（放弃）**：图片存 PG bytea → DB 膨胀、预览需转 base64；存 MinIO → 本期不引入对象存储复杂度（用户已定案本地磁盘）。

### D8. LLM Provider 抽象 + 开关门控 + 降级

- **决议**：`app/services/llm_providers/`（base / factory / dashscope_provider / local_openai_provider，**复用 ASR Provider 的并发加锁懒加载模式**）+ `app/services/llm_service.py`：
  - **门控**：`KB_LLM_ANSWER_ENABLED`（默认 false）&& 请求 `answer=true` && 检索未触发双闸门兜底（results 非空）才调用；
  - **Provider 契约**：`generate(context: list[SourceItem], query: str, timeout: float) -> LLMAnswer(text: str, citations: list[str])`——citations 为命中文档 doc_id 列表，回答文本基于召回片段组装（prompt 注入 chunk 原文 + 页码 + "仅依据给定资料回答，不得编造"约束），遵守全局护栏（04_llm_guardrail【强制1】双校验、03_db_vector_spec【强制8】脱敏、05_prompt_spec【强制2/6】配置化）；
  - **合规红线**：检索片段中手机号/金额等敏感信息注入 LLM 前脱敏为占位符；调用前置合规拦截 + 后置内容校验双机制，命中红线则丢弃回答按降级处理；回答 Prompt 由 `conf/llm_answer_prompt.yaml` 配置化管理，chunk 文本/页码/query 参数注入，**禁止硬编码 Prompt 明文**；
  - **并发上限**：LLM 调用受 `LLM_MAX_CONCURRENCY`（默认 4）信号量约束，超限按降级路径处理（`answer=null` + `degraded=true`）；
  - **降级**：超时（`LLM_TIMEOUT` 默认 5s）/网络异常/Provider 不可用 → 捕获并返回 `answer=None`，检索层标 `degraded=true`，**不抛错**；连续 3 次失败置熔断标识 `rag:llm:degraded`（TTL 60s，与既有 `rag:embed:degraded` 同风格），窗口内短路；
  - **日志**：`log_service.py` 扩展 kb_search_logs 写入（LLM 调用标记列：发起/降级，新增列走迁移 0008，`VARCHAR+CHECK` 对齐类型规范）；
  - **缓存**：LLM 回答为动态内容，**不进查询缓存**（缓存仅覆盖检索片段，key 语义与 V4 一致）；请求 `answer=true` 时**跳过缓存读取**（避免缓存响应缺 answer 语义悬空）直接走真实检索，且不写缓存。
- **备选（放弃）**：RAG 内直接嵌 DashScope SDK 裸调用 → 违反护栏/无 Provider 切换能力；回答走 SSE 流式 → 超范围。

### D9. 检索响应扩展：answer 与 results[].assets（纯增量字段）

- **关键 Diff 要点**：
  - `app/models/search.py`：`SearchRequest` 新增 `answer: bool = False`；`SearchResponse` 新增 `answer: str | None = None`；`SourceItem` 新增 `assets: list[AssetPreview] = []`（`AssetPreview`: asset_type/page/storage_path/mime_type）；
  - `app/api/search.py`：响应组装时从检索服务返回体取 answer 与 assets 填充（缺省 null/空数组，**不改变既有字段**）；
  - `app/services/search_service.py`：检索完成后，若门控满足 → 调 `llm_service`；并从 `asset_repo` 按命中 doc_id+chunk 关联取 assets 填充；
  - 开关关闭时：不调用 llm_service、不查 asset_repo（assets 保持空数组），响应与 V4 完全一致。
- **备选（放弃）**：新增独立 `/kb/answer` 端点 → 调用方需多一次调用且无法复用检索链路上下文；V5 以可选字段承载（对齐 PRD §5.2 `answer?: bool`）。

### D10. Gradio 浏览器前端（独立 compose 服务）

- **决议**：`services/rag-service/gradio/app.py`（+ `requirements` 并入全局 requirements.in）：
  - 上传页：gr.File（14 种扩展名白名单）+ 分类下拉 + 上传按钮 → 调 `POST /api/v1/kb/documents`（multipart，调用携带 `RAG_API_TOKEN`（env 注入，部署时由运维配置））→ 成功展示 doc_id/状态，失败 gr.Error 友好提示；
  - 问答页：文本输入 + top_k + "是否生成回答" 复选 → 调 `POST /api/v1/kb/search`（`answer=true` 仅当复选且开关开启时传）→ 渲染：问题 → 召回 chunk 列表（content/score/source/页码/图片预览 `gr.Image`/表格预览 `gr.Dataframe`）→ LLM 回答（`answer` 有值则展示，无值展示"回答暂不可用"提示）；
  - **预览链路**：Gradio 与 rag-service **共享挂载** `KB_ASSETS_DIR` 数据卷（`rag_assets`）——图片按 `kb_doc_assets.storage_path` 相对路径读取渲染 `gr.Image`，表格按 `content_json` 渲染 `gr.Dataframe`；**不新增后端静态文件端点、不改动既有 API**；
  - **鉴权**：`RAG_API_TOKEN` env 注入（compose 配置，生产由运维提供），前端不硬编码 token；
  - 异常处理：所有后端调用包 try/except + 超时，后端不可用展示友好提示，不白屏；
  - **部署**：`docker-compose.yml`/`dev` 新增 `gradio` 服务（`build: deploy/gradio/`，`ports: ["7860:7860"]`，`depends_on: rag-service: condition: service_healthy`），`deploy/gradio/`（Dockerfile + app.py 或复用 rag 镜像启动 gradio 进程）；仅内部网络，不映射公网。
- **备选（放弃）**：Vue3 前端（M3 规划）→ V5 用 Gradio 快速交付浏览器入口，满足验收；rag-service 容器内副进程 → 日志/健康检查耦合，用户已定案独立服务。

### D11. 配置项与开关清单（全量）

`common/config/settings.py`（供全部 7 服务共用）+ `services/rag-service/Dockerfile` ENV + `.env.example` 同步登记：

| 键                         | 默认           | 说明                                       |
| :------------------------- | :------------- | :----------------------------------------- |
| `KB_LLM_ANSWER_ENABLED`    | false          | LLM 回答开关（关闭=V4 行为，零 LLM 调用）  |
| `LLM_PROVIDER`             | dashscope      | dashscope 在线 / local OpenAI 兼容         |
| `LLM_TIMEOUT`              | 5s             | LLM 回答超时（超时→降级纯检索）            |
| `LLM_MAX_CONCURRENCY`      | 4              | LLM 回答并发上限（信号量，超限降级）       |
| `LLM_LOCAL_BASE_URL`       | 空             | local Provider 基址（未配置按不可用降级）  |
| `LLM_LOCAL_API_KEY`        | 空             | local Provider 密钥                        |
| `LLM_MODEL`                | 空             | 本地模型名（dashscope 下按供应商默认模型） |
| `KB_IMAGE_EXTRACT_ENABLED` | false          | 内嵌图片抽取 + OCR 文搜图                  |
| `KB_TABLE_EXTRACT_ENABLED` | false          | 内嵌表格结构化解析                         |
| `KB_EXCEL_SHEET_ALL`       | true           | Excel 全部 sheet 入库                      |
| `KB_EXCEL_HEADER_ROW`      | 1              | Excel 表头行号                             |
| `KB_ASR_AMR_WMA_ENABLED`   | false          | AMR/WMA 解码接入开关                       |
| `KB_ASSETS_DIR`            | `data/assets/` | 内嵌资产本地存储目录（Docker 挂载 volume） |
| `KB_ASSETS_MAX_SIZE`       | 1024           | 图片渲染最大边长（px，等比缩放）           |

## API 变更对照表（新增/不变）

| 接口                                                                                             | 变更类型             | 说明                                                                                                               |
| :----------------------------------------------------------------------------------------------- | :------------------- | :----------------------------------------------------------------------------------------------------------------- |
| `POST /kb/documents`                                                                             | 白名单扩展           | 新增 `.xlsx/.pptx/.amr/.wma`（其余行为不变；格式白名单外 → A3001）                                                 |
| `POST /kb/search`                                                                                | 新增可选字段         | 请求新增 `answer?: bool`（缺省 false）；响应新增 `answer`（可空）与 `results[].assets`（可空数组）；既有字段零改动 |
| `GET /kb/documents`、`GET /kb/documents/{doc_id}`、`DELETE`、reindex、sensitive-words、`/health` | **完全保持原有不变** | 路径、请求/响应包、错误码零改动                                                                                    |
| Gradio 页面                                                                                      | **新增（非 REST）**  | 独立服务 7860，复用上述 API                                                                                        |

## Risks / Trade-offs

- [xlsx/pptx/内嵌资产解析格式多样性（合并单元格、矢量图、备注缺失）] → 全部开关门控 + 解析失败按既有 A3001/跳过资产路径，不阻断正文；开关关闭=V4 行为。
- [ffmpeg 系统依赖引入] → Dockerfile apt 安装 ffmpeg（rag 镜像 + gradio 镜像）；解码失败捕获为业务错误，不卡 worker。
- [LLM 回答不确定性与数据出网合规] → 开关默认关 + 请求显式开启才调用；回答基于召回片段组装（防编造）；敏感信息注入前脱敏 + 前置拦截/后置校验双机制（对齐 03/04/05 全局护栏）；超时/熔断/不可用一律降级纯检索；DashScope 为既有供应商（ASR/Query 改写已使用）。
- [kb_doc_assets 与本地文件一致性] → doc_id 维度绑定 + 删除/reindex 同步清理 + 失败走既有补偿契约；本地目录经 Docker volume 持久化。
- [响应新增字段兼容性] → JSON 新增可空字段对存量调用方完全兼容（服务端新增字段不影响旧客户端解析）；answer/assets 缺省 null/空数组。
- [Gradio 鉴权] → 生产环境需注入 ADMIN token 调上传/检索；开发环境内部网络直连；不接公网。

## Migration Plan

1. **数据库**：Alembic 迁移 `0008_kb_doc_assets.py`（新表 `kb_doc_assets` 含 `content_json` 结构化预览列 + kb_search_logs LLM 标记列，VARCHAR+CHECK；版本号接续 0007 下一个）；**同步 step4/01 全局 DDL 单一事实源收录 `kb_doc_assets`**（对齐 V2 先例）。
2. **依赖**：`requirements.in` 取消注释/新增 `openpyxl`、`python-pptx`、`PyMuPDF`、`pydub`、`gradio`，重新编译 `requirements.lock`（全局单文件，7 服务共用）；Dockerfile apt 增加 `ffmpeg`。
3. **代码**：按 tasks.md 增量实现（配置 → 迁移 → 解析器 → 资产服务 → LLM 服务 → 检索响应 → Gradio → 测试/冒烟）。
4. **部署**：docker-compose（base/dev）新增 `gradio` 服务 + rag-service/gradio 共享挂载 assets volume（`rag_assets`）+ 新开关 ENV + `RAG_API_TOKEN` 注入。
5. **回滚**：全部行为改动以开关隔离，关闭新增开关即恢复 V4 行为；`kb_doc_assets` 为新增表、assets 为新增目录，回滚零成本、无 schema 回退；LLM 开关默认关，停用即零调用。

## Open Questions

- `LLM_TIMEOUT=5s` 与回答长度（token 数）的匹配度、`KB_ASSETS_MAX_SIZE=1024px` 的图片质量/体积平衡，留待 apply 后冒烟/评测用数据决策，不影响本变更 spec 与任务拆分。
- 已收敛：Gradio 鉴权定为 `RAG_API_TOKEN` env 注入（生产由运维提供，前端不硬编码）；表格预览定为 `content_json` 结构化数据 + 共享数据卷渲染，不再依赖文本还原。

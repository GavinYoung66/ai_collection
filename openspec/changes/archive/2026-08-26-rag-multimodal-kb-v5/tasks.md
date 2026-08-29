# RAG 多模态知识库 V5 — 任务清单

> 实施原则：**增量开发，零副作用**。不改动 V1~V4 已确认的对外接口契约、既有表列语义、Milvus/ES schema 与既有业务主逻辑；新能力全部以开关接入（默认 false），关闭开关 = V4 完全行为；错误码全集 84 条禁新增；Milvus/ES 不新增字段、不重建索引。每项任务标注【新增】/【修改】及文件路径，修改已有文件标注具体位置与改动原因。参考 spec（document-ingest / multimodal-ingest / hybrid-search / gradio-frontend / llm-answer）与 design.md（D1~D11）。

## 0. 全局实现约束（代码实现阶段强制，最高优先级）

> 以下约束适用于 V5 全部代码实现任务，任何任务不得突破；评审优化清单【高优先级】已逐项合并入对应 spec / design / 本清单条目（见 §13.6 变更 Diff 摘要）。

1. **禁止一次性输出全部完整代码**：代码实现阶段严禁一次性生成整个模块/服务的全部源码文件，严禁批量落盘未到期的任务代码；
2. **必须按任务拆分、分阶段、小批次迭代输出变更 Diff**：实现顺序严格跟随本清单任务编号（配置 → 迁移 → 解析器 → 资产服务 → LLM 服务 → 检索响应 → Gradio → 测试/冒烟），每批次仅输出该任务对应的变更 Diff（新增/修改文件 + 关键代码片段），未轮到的任务不得提前实现；
3. **每一批变更 Diff 必须附带对应模块的冒烟测试要点**：列出该批次代码可独立验证的要点（解析器样例输入输出、接口响应断言、错误码路径、降级行为等），本批次验证通过后再进入下一任务；
4. **只做增量，严禁修改 V4 已交付业务逻辑与对外 API**：不修改 V1~V4 已确认的对外接口路径/请求/响应包/错误码/既有表列语义/Milvus·ES schema/既有业务主逻辑；新能力全部以开关接入（默认 false），关闭开关 = V4 完全行为；错误码全集 84 条禁新增；
5. **每完成一个任务立即勾选**（`- [ ]` → `- [x]`）并附验证结论；未实现或未验证的任务禁止提前勾选。

## 1. 配置与开关

- [x] 1.1 【修改】`common/config/settings.py`：新增 14 个配置项（与既有 `KB_*` 开关同级，供全部 7 服务共用，含类型注解与注释）——`KB_LLM_ANSWER_ENABLED=False`、`LLM_PROVIDER="dashscope"`、`LLM_TIMEOUT`、`LLM_MAX_CONCURRENCY=4`、`LLM_LOCAL_BASE_URL=""`、`LLM_LOCAL_API_KEY=""`、`LLM_MODEL`、`KB_IMAGE_EXTRACT_ENABLED=False`、`KB_TABLE_EXTRACT_ENABLED=False`、`KB_EXCEL_SHEET_ALL=True`、`KB_EXCEL_HEADER_ROW=1`、`KB_ASR_AMR_WMA_ENABLED=False`、`KB_ASSETS_DIR="data/assets"`、`KB_ASSETS_MAX_SIZE=1024`（design D11）。**已落地定案：`LLM_MODEL`/`LLM_TIMEOUT` 在 V4 已存在（`LLM_MODEL=qwen-plus`、类默认 `LLM_TIMEOUT=10` 被本地 .env 覆盖为 5），经评审定案【复用既有配置、不改默认值，query_rewrite 依赖】，实际新增 12 项，settings 加载验证通过**
- [x] 1.2 【修改】`services/rag-service/Dockerfile`：新增 V5 功能开关 ENV 块（默认值同 settings，紧跟既有 V4 ENV 块之后，风格一致）——`KB_LLM_ANSWER_ENABLED=false`、`LLM_PROVIDER=dashscope`、`LLM_MAX_CONCURRENCY=4`、`LLM_LOCAL_BASE_URL=`、`LLM_LOCAL_API_KEY=`、`KB_IMAGE_EXTRACT_ENABLED=false`、`KB_TABLE_EXTRACT_ENABLED=false`、`KB_EXCEL_SHEET_ALL=true`、`KB_EXCEL_HEADER_ROW=1`、`KB_ASR_AMR_WMA_ENABLED=false`、`KB_ASSETS_DIR=data/assets`、`KB_ASSETS_MAX_SIZE=1024`（同 1.1 定案：LLM_MODEL/LLM_TIMEOUT 不设 ENV、复用既有默认，避免覆盖 V4）
- [x] 1.3 【修改】`.env.example`：登记上述 14 个配置项（含注释说明默认值语义；LLM_MODEL/LLM_TIMEOUT 复用既有条目，注释说明不重复定义）
- [x] 1.4 【修改】`services/rag-service/README.md`：开关清单补 14 个新开关（对齐既有开关登记风格，含 LLM 门控/资产抽取/新格式说明；LLM_MODEL/LLM_TIMEOUT 复用说明已在表头注明）

## 2. 依赖与基础环境（全局单文件 requirements）

- [x] 2.1 【修改】`requirements.in`：取消注释 `openpyxl==3.1.2`，新增 `python-pptx`、`PyMuPDF`、`pydub`、`gradio`（全局单文件，7 服务共用；锁定版本需与既有约束无冲突）。**已落地定案：`gradio` 与全局 V4 锁定硬冲突（需 fastapi>=0.115.2/python-multipart>=0.0.18/pillow<13 vs 既有 0.104.1/0.0.6/Pillow>=12.2.0），经定案【gradio 独立锁定于 deploy/gradio/requirements.txt，仅 gradio 容器使用】，全局仅加 openpyxl/python-pptx/PyMuPDF/pydub；pip-compile 解析验证通过**
- [x] 2.2 【修改】`requirements.lock`：在 conda 环境 `ai_collection` 下用 `pip-compile` 解析验证，提交锁定版本（langchain 等既有延后依赖保持不动）。**已落地：沿用仓库手工策展锁格式（非哈希全量锁），按解析结果增量登记 openpyxl/et-xmlfile/python-pptx/lxml/xlsxwriter/PyMuPDF/pymupdfb/pillow=12.3.0/pydub，既有 79 行条目零改动**
- [x] 2.3 【修改】`services/rag-service/Dockerfile`：apt 安装 `ffmpeg`（AMR/WMA 解码依赖，追加到既有 apt-get install 行）
- [x] 2.4 【核对】确认 `openpyxl/python-pptx/PyMuPDF/pydub` 纯 Python wheel 可安装（无额外系统库依赖；PyMuPDF 自带二进制）。**已核对：openpyxl/python-pptx/pydub 均为纯 Python wheel，pydub 零 Python 依赖；PyMuPDF 1.24.9 有 cp311 平台 wheel（macOS arm64 实测 + PyPI 提供 manylinux x86_64/aarch64），无 arm64 编译需求，无需 README 记录**

## 3. 数据模型与存储（kb_doc_assets + kb_search_logs 扩展）

- [x] 3.1 【新增】`migrations/versions/0008_kb_doc_assets.py`：**版本号 = 既有最大 revision（0007）的下一个**（禁止硬编码为 0008 之外的固定值）；新增表 `kb_doc_assets`（id BIGSERIAL PK / doc_id UUID FK kb_documents ON DELETE CASCADE / asset_type VARCHAR(10) + CHECK IN ('image','table') / page INT / storage_path VARCHAR(500) / ocr_text TEXT / mime_type VARCHAR(100) / content_json TEXT（表格行列结构化 JSON，图片为 NULL）/ 公共字段 create_time,update_time,is_delete，索引 (doc_id)、(doc_id,asset_type)）；`kb_search_logs` 增列 `llm_called CHAR(1) DEFAULT '0'`、`llm_degraded CHAR(1) DEFAULT '0'`（VARCHAR+CHECK，对齐全局类型规范，默认值不强制回填）
- [x] 3.2 【修改】`services/rag-service/app/models/orm.py`：新增 `KbDocAsset` ORM（字段/类型/注释与 3.1 迁移对齐，含 content_json）；`KBSearchLog` 增 `llm_called`/`llm_degraded` 字段（CHAR(1) 默认 '0'），补 docstring
- [x] 3.3 【新增】`services/rag-service/app/repositories/asset_repo.py`：`batch_create(assets)` / `delete_by_doc(doc_id)` / `get_by_doc(doc_id)` / `get_by_doc_page(doc_id, page)`（纯 CRUD，无业务逻辑）
- [x] 3.4 【修改】`services/rag-service/app/repositories/doc_repo.py`：删除文档/`reindex` 时级联清理 `kb_doc_assets`（记录删除 + 返回 doc_id 供本地文件清理），失败走既有"删除失败重试与补偿"契约

## 4. 文档格式解析（Excel / PPT）

- [x] 4.1 【修改】`services/rag-service/app/parsers/base.py`：`DocumentParser` 抽象新增可选方法 `extract_with_assets(file_path) -> tuple[str, list]`（默认实现委托 `extract_text()` 返回 `(text, [])`），**既有 `extract_text` 签名与 6 个既有 Parser 零改动**，补 docstring
- [x] 4.2 【新增】`services/rag-service/app/parsers/xlsx_parser.py`：Excel 解析（design D2）——全部 sheet（`KB_EXCEL_SHEET_ALL` 控制）/ 首行表头（`KB_EXCEL_HEADER_ROW`）/ 行切块序列化 `"sheet名: 列名1:值1, 列名2:值2,..."` / 空行与全空值行跳过 / 损坏或加密 → A3001；解析耗时受 `KB_PARSE_TIMEOUT` 约束
- [x] 4.3 【新增】`services/rag-service/app/parsers/pptx_parser.py`：PPT 解析（design D3）——每页 标题+正文(文本框)+演讲者备注（并入页文本）/ 页码与版式占位文字保守启发式过滤（页码行 `^\s*\d+\s*$` + 已知占位文案表，防误删正文）/ 多页按序拼接
- [x] 4.4 【修改】`services/rag-service/app/parsers/__init__.py`：`SUPPORTED_EXTENSIONS` 增 `.xlsx/.pptx/.amr/.wma`；`ParserFactory` 注册表新增 `.xlsx→XlsxParser`、`.pptx→PptxParser`，`.amr/.wma→AudioParser`（复用既有音频分支）；白名单外（含 .exe）→ A3001 逻辑不变
- [x] 4.5 【修改】`services/rag-service/app/services/ingest_service.py`：新增格式 source 打标——`.xlsx→"excel"`、`.pptx→"ppt"`（随 kb_chunks/kb_documents 写入透传），回滚契约与 V1 完全一致（PG 成功但索引失败 → 回滚 + status=failed + A3003）

## 5. 内嵌图片/表格抽取与存储（文搜图）

- [x] 5.1 【新增】`services/rag-service/app/parsers/asset_extractor.py`：PDF/DOCX/PPTX 内嵌图片抽取（design D4）——PDF 用 PyMuPDF `page.get_images` 抽位图、矢量图经渲染接口转位图；DOCX 用 python-docx `inline_shapes` 取二进制；PPTX 复用 D3 解析的图片部件；Pillow 等比缩放至 `KB_ASSETS_MAX_SIZE`；返回资产列表（type/page/binary/mime）
- [x] 5.2 【新增】`services/rag-service/app/services/asset_service.py`：资产落库编排——图片调既有 `ocr_client.py`（ocr-service:8200，30s 超时/3 次退避/熔断 `rag:ocr:degraded`）识别文字 → OCR 文本以 chunk 走既有双索引入库（source=`asset_image`，chunk 前缀 `[图片] 页码P{n}: `）→ 图片文件写 `KB_ASSETS_DIR/{doc_id}/{page}_{idx}.png` → `kb_doc_assets` 落元数据（asset_type=image，storage_path/ocr_text/mime_type）；单张失败仅 warning（含 trace_id）+ 跳过，不阻断正文
- [x] 5.3 【修改】`services/rag-service/app/parsers/asset_extractor.py`：内嵌表格结构化解析（design D5）——PDF 用 pdfplumber `extract_tables()`、DOCX 用 `doc.tables`、PPTX 用 table shape；每行序列化 `"表格(第{page}页): 列名1:值1,..."` 为 chunk（保留行列结构不打散）；合并单元格取左上角值 + 行内保留（启发式）；表格元数据落 `kb_doc_assets`（asset_type=table，**content_json 存行列结构化 JSON 供 gr.Dataframe 渲染**）
- [x] 5.4 【修改】`services/rag-service/app/services/ingest_service.py`：正文 chunk 双索引完成后，若 `KB_IMAGE_EXTRACT_ENABLED`/`KB_TABLE_EXTRACT_ENABLED` 任一开启且解析器返回 assets → 调 `asset_service` 落库（资产失败仅 warning 不回滚正文）；开关全关时整段短路（零开销，行为与 V4 一致）
- [x] 5.5 【修改】`services/rag-service/app/api/documents.py`（删除/reindex 路径）与 `doc_repo.py`：删除/reindex 时清理 `kb_doc_assets` 记录 + `KB_ASSETS_DIR/{doc_id}/` 本地目录（失败按既有补偿契约：重试 2 次 + 告警标记）

## 6. 音频格式扩展（AMR / WMA）

- [x] 6.1 【新增】`services/rag-service/app/services/audio_decoder.py`：ffmpeg 解码（subprocess，超时受 `KB_PARSE_TIMEOUT` 约束）AMR/WMA → 临时 WAV，用后即删；解码失败抛业务异常（供上层转 A3001）
- [x] 6.2 【修改】`services/rag-service/app/parsers/audio_parser.py`：`.amr/.wma` 分支——`KB_ASR_AMR_WMA_ENABLED=true` 时先解码为 WAV 再走既有 VAD 静音裁剪 + ≤60s 分片 + ASR Provider 转写管线（Provider 契约零改动）；**解码/转写异常捕获为业务错误**（A3001 或 `KB_OCR_ASR_DEGRADED_KEEP_TEXT=true` 降级占位），worker 不卡死、不影响后续请求；开关关闭 → 白名单外 A3001（V4 行为）
- [x] 6.3 【修改】`services/rag-service/app/services/ingest_service.py`：AMR/WMA 走音频 source=asr 打标（与 MP3/WAV/M4A 一致），回滚契约不变（注：随 4.4/4.5 `.amr/.wma→AudioParser→asr` 分支一并覆盖，无独立代码改动）

## 7. LLM 回答服务（开关门控 + Provider 抽象）

- [x] 7.1 【新增】`services/rag-service/app/services/llm_providers/`（base.py / factory.py / dashscope_provider.py / local_openai_provider.py）：Provider 抽象（design D8，复用 ASR Provider 并发加锁懒加载模式）——统一契约 `generate(context, query, timeout) -> LLMAnswer(text, citations: list[doc_id])`；dashscope 默认 / local OpenAI 兼容（读 `LLM_LOCAL_BASE_URL`/`LLM_LOCAL_API_KEY`/`LLM_MODEL`，未配置基址按不可用降级）；切换 `LLM_PROVIDER` 不影响对外请求/响应契约
- [x] 7.2 【新增】`services/rag-service/app/services/llm_service.py`：门控 + 降级编排——`KB_LLM_ANSWER_ENABLED=true` 且请求 `answer=true` 且检索未兜底（results 非空）才调用；**合规红线**：注入 LLM 前对 chunk 文本脱敏（手机号/金额→占位符）+ 前置合规拦截 + 后置内容校验（命中红线丢弃回答按降级处理）；`LLM_TIMEOUT` 超时/网络异常/不可用 → 捕获返回 `answer=None`（不抛错）；连续 3 次失败置熔断标识 `rag:llm:degraded`（TTL 60s，复用既有熔断风格），窗口内短路；**并发上限**：`LLM_MAX_CONCURRENCY`（默认 4）信号量，超限按降级处理
- [x] 7.3 【修改】`services/rag-service/app/services/log_service.py`：kb_search_logs 写入新增 `llm_called`/`llm_degraded` 标记（对齐 3.1 迁移列）；写入失败不阻断主流程（既有容错不变）
- [x] 7.4 【新增】`services/rag-service/conf/llm_answer_prompt.yaml`：回答生成 Prompt 模板（占位符：{context} 召回文本、{query}、页码），业务变量参数注入，禁止代码硬编码 Prompt（对齐 05_prompt_spec【强制2/6】；conf/ 已随既有 Dockerfile COPY 进镜像）

## 8. 检索响应扩展（answer / assets 可选字段）

- [x] 8.1 【修改】`services/rag-service/app/models/search.py`：`SearchRequest` 增 `answer: bool = False`（Field(description=...)）；`SourceItem` 增 `assets: list[AssetPreview] = []`；`SearchResponse` 增 `answer: str | None = None`；新增 `AssetPreview` 模型（asset_type/page/storage_path/mime_type，缺省空数组），补 docstring（来源与开关语义）
- [x] 8.2 【修改】`services/rag-service/app/services/search_service.py`：检索完成后——命中结果按 doc_id 关联 `asset_repo.get_by_doc` 填充 `results[].assets`（资产抽取开启且存在时，否则空数组，不改变既有排序/召回逻辑）；若 LLM 门控满足 → 调 `llm_service` 生成回答，`answer` 写入返回体；LLM 降级时置 `degraded=true` 且 `answer=None`；开关关闭时不查 asset_repo、不调 LLM（零开销）
- [x] 8.3 【修改】`services/rag-service/app/api/search.py`：响应组装透传 `answer` 与 `results[].assets`（缺省 null/空数组，既有字段零改动）；`answer=true` 请求**跳过缓存读取**（避免缓存响应缺 answer 语义悬空）且不写缓存，其余请求缓存语义与 V4 一致

## 9. Gradio 浏览器前端（独立 compose 服务）

- [x] 9.1 【新增】`services/rag-service/gradio/app.py`：上传页——`gr.File`（14 种扩展名白名单 txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma）+ 分类下拉 + 上传按钮，调用 `POST /api/v1/kb/documents`（multipart，请求头携带 `RAG_API_TOKEN`（env 注入）），成功展示 doc_id/status，失败 `gr.Error` 友好提示（错误码/原因），**不白屏、不裸 500**
- [x] 9.2 【新增】`services/rag-service/gradio/app.py`：问答页——输入 + top_k + "生成回答"复选 → 调用 `POST /api/v1/kb/search`（复选且开关开启时传 `answer=true`）；渲染"问题 → 召回 chunk 列表（content/score/source/页码/图片 `gr.Image` 预览/表格 `gr.Dataframe` 预览）→ LLM 回答（`answer` 有值展示，无值展示"回答暂不可用"提示）"；**预览源**：图片按 `kb_doc_assets.storage_path` 从共享挂载的 `KB_ASSETS_DIR` 数据卷读取，表格按 `content_json` 渲染 `gr.Dataframe`；所有后端调用 try/except + 超时，后端宕机展示友好提示
- [x] 9.3 【新增】`deploy/gradio/Dockerfile`：Gradio 独立服务镜像（公共基础镜像 + 全局 requirements 安装 + COPY `services/rag-service/gradio/` + 启动 `python app.py`，监听 7860；挂载点对齐 `KB_ASSETS_DIR` 共享数据卷）
- [x] 9.4 【修改】`services/rag-service/README.md`：Gradio 使用说明（启动方式、页面地址、内部网络访问约束、`RAG_API_TOKEN` 配置说明）

## 10. docker-compose 部署增量（gradio 服务 + assets 数据卷）

- [x] 10.1 【修改】`docker-compose.yml`（base）与 `docker-compose.dev.yml`（dev override）：新增 `gradio` 服务 + rag-service 挂载 assets 数据卷，增量片段如下（对接既有缩进/命名风格）：

```yaml
# ===== V5 新增：Gradio 浏览器前端（独立进程，仅内部网络）=====
gradio:
  build:
    context: .
    dockerfile: deploy/gradio/Dockerfile
  ports:
    - "7860:7860"
  environment:
    - RAG_API_BASE=http://rag-service:8000/api/v1
    - RAG_API_TOKEN=${RAG_API_TOKEN:?must set internal token}
    - GRADIO_SERVER_NAME=0.0.0.0
    - KB_ASSETS_DIR=/app/data/assets # 与 rag-service 共享资产数据卷（预览源），gradio app.py 读取同一 env 名
  volumes:
    - rag_assets:/app/data/assets # 与 rag-service 共享资产数据卷（预览源）
  depends_on:
    rag-service:
      condition: service_healthy
  restart: unless-stopped

# rag-service 增量：本地磁盘资产目录（图片/表格预览源），与 gradio 共享同一数据卷
#   volumes:
#     - rag_assets:/app/data/assets
# 顶部 volumes 增量：
#   rag_assets:
```

- [x] 10.2 【修改】`docker-compose.dev.yml`：rag-service 与 gradio 均挂载 `KB_ASSETS_DIR`（开发热更新：`- ./services/rag-service/data/assets:/app/data/assets`），保证两容器共享同一预览源；若 dev 环境不依赖 ocr-service（既有注释说明），内嵌图片抽取冒烟步骤按 WARN 放行（对齐既有 OCR 冒烟处理）
- [x] 10.3 【核对】`docker compose config` 校验通过（无 schema/依赖错误），`gradio` 服务在 rag-service healthy 后启动

## 11. 单元测试（新增能力全覆盖，存量基线不回退）

- [x] 11.1 【新增】`tests/test_xlsx_parser.py`：多 sheet 全入库 / sheet 名前缀冗余 / 行切块序列化 / 空行全空值跳过 / 损坏 xlsx → A3001 / `KB_EXCEL_SHEET_ALL=false` 仅首 sheet。**已实现 7 用例（openpyxl 真实 xlsx）全绿**
- [x] 11.2 【新增】`tests/test_pptx_parser.py`：标题+正文+备注并入页文本 / 页码与版式占位过滤（不产生独立 chunk）/ 正文含数字不误删 / 备注可检索命中。**已实现 8 用例（python-pptx 真实 pptx）全绿**
- [x] 11.3 【新增】`tests/test_asset_extractor.py`：PDF/DOCX/PPTX 内嵌图片抽取（含矢量图渲染位图路径 mock）/ 图片 OCR 文本 chunk 入库 source=asset_image（mock ocr-service）/ kb_doc_assets 元数据落表 / 单张失败不影响正文 / 开关关闭零资产。**已实现 9 用例全绿**
- [x] 11.4 【新增】`tests/test_table_extract.py`：内嵌表格行列结构保留 / 任意单元格命中 / 表格预览元数据返回 / 开关关闭走 V4 文本化。**已实现 4 用例全绿**
- [x] 11.5 【新增】`tests/test_audio_amr_wma.py`：AMR/WMA ffmpeg 解码 → 复用 VAD/ASR 管线转写入库（mock 解码与 ASR Provider）/ 损坏音频 → A3001 或降级占位且 worker 不卡死 / `KB_ASR_AMR_WMA_ENABLED=false` → 白名单外 A3001。**已实现 5 用例全绿**
- [x] 11.6 【新增】`tests/test_llm_answer.py`：开关关闭零 LLM 调用响应与 V4 一致 / 开关开启+answer=true 生成带溯源回答 / 开关开启但未传 answer 不调用 / 双闸门兜底不调用 / LLM 超时/熔断降级 `answer=null+degraded=true` / 熔断标识 `rag:llm:degraded` TTL 60s / **敏感信息脱敏后注入 LLM（手机号/金额→占位符）** / **前置合规拦截命中红线上下文不发起 LLM 调用（answer=null+degraded=true，零调用）** / **后置内容校验拦截违规回答按降级处理** / **Prompt 从 `conf/llm_answer_prompt.yaml` 加载且业务变量参数注入（无硬编码）** / **`LLM_MAX_CONCURRENCY` 并发上限** / **缓存命中但 answer=true 跳过缓存走真实检索**。**已实现 15 用例全绿（并发上限语义：信号量排队限流，与实现 D8 一致）**
- [x] 11.7 【新增】`tests/test_search_response_assets.py`：`results[].assets` 填充/空数组两态 / 表格资产 storage_path 指向 content_json 预览文件（gr.Dataframe 渲染源）/ 响应 `answer` 字段缺省 null / 存量调用方不传 answer 行为不变。**已实现 6 用例全绿**
- [x] 11.8 【新增】`tests/test_asset_cleanup.py`：删除文档清理 kb_doc_assets 记录 + 本地目录 / reindex 先清后建 / 清理失败告警补偿。**已实现 5 用例全绿**
- [x] 11.9 【回归】全量单测：基线 240 passed / 1 skipped **不回退**；新增用例全绿；ruff/bandit/mypy 不劣于基线（rag-service 目录新增文件零违规）。**实测 303 passed / 1 skipped（基线 240 + 新增 63，零回退）；ruff 0 错误（修复 factory.py **all** F822 + 2 处测试 unused import）；bandit 新增生产代码 0 issue（测试文件 B101 assert 与既有基线同质 Low）；mypy 按 Makefile 仅查 common/（本次未改动，基线不变）**

## 12. 冒烟脚本（全链路，含 Gradio 页面检查）

- [x] 12.1 【新增】`scripts/smoke/smoke_rag_v5.sh`（`bash -n` 通过），覆盖 12 步全链路检查：
  1. `/health` 探活（PG/Milvus/Redis/embedding）
  2. 上传 TXT（ADMIN token）→ 列表 → 详情 → 检索 → 删除（V1 基线回归，含 status 轮询 active）
  3. 上传 `.xlsx`（多 sheet）→ 检索命中具体行（source=excel 断言，sheet2 命中验证 `KB_EXCEL_SHEET_ALL=true`）
  4. 上传 `.pptx`（含备注）→ 检索命中备注内容（source=ppt 断言）
  5. 上传 `.amr`（ffmpeg 解码）→ 转写入库 source=asr（开关/ASR 不可用时 WARN 放行，对齐既有 ASR 冒烟处理）
  6. 上传损坏音频 → 断言 A3001 业务错误且服务后续请求正常（worker 不卡死）
  7. 内嵌图片抽取（`KB_IMAGE_EXTRACT_ENABLED=true`）→ assets 含 image + storage_path 预览元数据返回（OCR 依赖不可用 WARN 放行）
  8. 内嵌表格抽取（`KB_TABLE_EXTRACT_ENABLED=true`）→ 单元格命中 + assets 含 table + storage_path 指向 .json（gr.Dataframe 渲染源）
  9. 检索 `answer=true`（开关开启）→ 返回 `answer` 带溯源引用 / 无 LLM 环境断言 `answer=null + degraded=true` 降级正确
  10. 检索 `answer=true`（开关关闭默认部署）→ 响应与 V4 完全一致（answer=null 零 LLM 调用，V5 契约字段恒存在）
  11. 删除含资产文档 → kb_doc_assets 计数为 0 + 本地资产目录清理（docker exec PG 查询 + 容器内目录检查，不可用 WARN）
  12. Gradio 页面可达性检查（`curl -s -o /dev/null -w "%{http_code}" http://localhost:7860/` = 200，不可达 WARN 放行）
      **已实现：脚本 + 样本生成段实跑验证（openpyxl/python-pptx/PIL/docx-zip 全部产出可解析文件）；`bash -n` 通过**
- [x] 12.2 【修改】`scripts/smoke/smoke_all.sh`：接入 `smoke_rag_v5.sh`（若存在则并入全量冒烟，V5 组件不可用时脚本内部 WARN 放行）
- [x] 12.3 【修改】`services/rag-service/README.md`：新增 §F V5 冒烟指引（12 步说明 + 预期结果表 + 运行方式 + 前置条件）

## 13. 封版与同步

- [x] 13.1 【修改】`services/rag-service/README.md`：新增 V5 章节（能力清单、API 变更表、数据模型变更 `kb_doc_assets`（含 content_json）、开关清单（14 项）、Gradio 使用与 `RAG_API_TOKEN` 配置、迁移 Runbook、回滚说明），与 design.md 对齐。**已落地：新增 §G「V5 多模态知识库能力总览」（功能清单 D1~D11 对照 / API 变更表 / kb_doc_assets 与 kb_search_logs 数据模型 / 14 项开关清单汇总 / Gradio 与 RAG_API_TOKEN 交叉引用 §E / V5 Migrate Runbook / 回滚说明），与 design.md 逐项对齐；顺带修正 §C 中 `LLM_MAX_CONCURRENCY` 描述为"超限排队等待，不降级"（与批 11 定案一致）**
- [x] 13.2 【修改】全局 DDL 单一事实源（step4/01 收录文档）：**收录 `kb_doc_assets` 新表定义**（对齐 V2 收录先例，含 content_json 列），Pydantic ORM 与 DDL 双向 CI 校验脚本扩展覆盖新表（对齐 03_db_vector_spec【强制9】）。**已落地：step4/01 修订记录 v1.1 + 表清单行 + 完整 DDL 段 8.1（kb_doc_assets 对齐迁移 0008，含 content_json/storage_path/ocr_text 列注释）；新增 `scripts/rag/check_orm_ddl.py` 双向校验脚本（SQL 词法 + AST 提取 + 类型归一化 + 表/列/类型/nullable/索引/CHECK 检查项，kb_search_logs 豁免 WARN），实跑 PASS：0 违规，kb_doc_assets 11 列全维度严格校验 + 双索引双向一致 + CHECK 声明通过**
- [x] 13.3 【核对】`scripts/rag/check_error_codes.sh` / `check_nongoals.sh` 无违规（错误码白名单 0 违规、Non-Goals 防呆 0 违规；V5 不新增错误码、不触碰 Non-Goals 清单）。**已实跑核对：check_error_codes.sh PASS（0 违规）、check_nongoals.sh PASS（0 违规）；V5 全程未新增错误码（白名单 A3001-A3006+B0001-B0010 之外零引用）、未触碰 Non-Goals 清单**
- [x] 13.4 【核对】`openspec validate` 本变更工件校验通过；`openspec status --change rag-multimodal-kb-v5` 全量 done。**已实跑核对：`openspec validate rag-multimodal-kb-v5 --type change --no-interactive` → Change is valid；`openspec status --change rag-multimodal-kb-v5` → 4/4 artifacts complete（proposal/specs/design/tasks 全勾选）**
- [x] 13.5 【归档】评审通过后经 `/opsx-sync` 将 5 个 delta 合入主 spec（document-ingest / multimodal-ingest / hybrid-search / 新增 gradio-frontend / 新增 llm-answer），变更归档至 `openspec/changes/archive/`，归档前保持本变更文件不变。**已执行：5 个 delta 经智能合并入主 spec（document-ingest 更新文档上传 requirement + 新增 Excel/PPT/kb_doc_assets 3 requirement；multimodal-ingest 更新音频 ASR requirement + 新增图片/表格抽取 2 requirement；hybrid-search 新增 answer/assets 响应扩展；新建 gradio-frontend / llm-answer 主 spec），`openspec validate --specs` 11/11 通过；变更已归档至 `openspec/changes/archive/2026-08-26-rag-multimodal-kb-v5/`。注意：llm-answer 主 spec 并发上限按批 11 用户裁决与实现 D8 写为"超限排队等待，不降级"（与 delta 原文"按降级处理"的差异为裁决性修正，design/tasks/代码已先行一致）**

## 13.6 变更 Diff 摘要（评审优化清单合并记录）

> 评审优化清单【高优先级】逐项合并结论（仅记录**增量修改**；已覆盖项不重复改动，防止无依据重构）：

| #   | 清单项                                               | 定案/增量                                                       | 本次修改位置                                                                                 |
| :-- | :--------------------------------------------------- | :-------------------------------------------------------------- | :------------------------------------------------------------------------------------------- |
| 1   | llm-answer 合规红线：脱敏→参数注入→前置拦截+后置校验 | 增量补全                                                        | spec `llm-answer` 新增 Scenario「前置合规拦截拦截违规上下文」；本清单 §11.6 补前置拦截测试点 |
| 2   | 缓存命中 + answer=true 行为                          | 已定案：跳过缓存读取 + 不写缓存（二选一定案）                   | 无改动（hybrid-search / llm-answer spec 场景 + §8.3/§11.6 已覆盖）                           |
| 3   | Gradio 预览链路闭合 + compose 片段                   | 已定案：共享数据卷 `rag_assets`（不新增静态端点）               | §10.1 compose 片段 env 变量名 `ASSETS_DIR`→`KB_ASSETS_DIR` 对齐 spec/design                  |
| 4   | kb_doc_assets 结构化预览载体                         | 已定案：`content_json` 列（表格行列 JSON，gr.Dataframe 渲染源） | 无改动（document-ingest / multimodal-ingest / D5/D7 / §3.1 已覆盖）                          |
| 5   | 封版段 step4/01 DDL 收录 + CI 双向校验               | 已覆盖                                                          | 无改动（§13.2 + design Migration Plan 已覆盖）                                               |
| 6   | LLM local Provider 配置项 + 并发上限                 | 已覆盖                                                          | 无改动（llm-answer spec Provider 场景 + D8/D11 + §1.1/§7.1 已覆盖）                          |
| 7   | Prompt 落 conf/llm_answer_prompt.yaml 配置化         | 已覆盖                                                          | 无改动（llm-answer spec + §7.4 已覆盖）                                                      |
| 8   | Gradio 鉴权收敛为落地任务                            | 已定案：`RAG_API_TOKEN` env 注入（生产运维配置，前端不硬编码）  | 无改动（gradio-frontend spec + D10 + §9.1/§10.1 已覆盖）                                     |

**原始需求保留核对**：V5 全部原始业务需求（Gradio 上传/问答全链路可视化、Excel/PPT 解析、PDF/PPT/Word 内嵌图片与表格抽取、AMR/MP3/WMA/WAV 音频转写入库、LLM 开关门控回答）及已定义可测验收标准在 proposal / spec / design / tasks 中完整保留，未删减、未篡改。

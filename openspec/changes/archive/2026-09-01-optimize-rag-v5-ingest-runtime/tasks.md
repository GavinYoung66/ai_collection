# Tasks

实现清单，按依赖顺序。每个任务绑定 delta spec 场景。本变更是 V5 增量修复，禁止改 archive、禁止改 POST 同步契约、禁止回退工作区已有的 DOCX media 补漏与 Gradio ReadTimeout 文案。

## 1. 图/表抽取独立门控

绑定 spec：`rag-service/multimodal-ingest`（MODIFIED 图片/表格独立门控 Scenario）
参考 design 决策 1。

- [x] 1.1 抽取层按开关分类型：图片抽取只看 `KB_IMAGE_EXTRACT_ENABLED`，表格抽取只看 `KB_TABLE_EXTRACT_ENABLED`；两开关全关仍短路不走 extract_with_assets。验证：只开图不开表不产生 table 资产；只开表不开图不产生 image 资产。
- [x] 1.2 落库层再过滤：`process_assets` 丢弃与当前开关不符的类型，避免漏网资产写入 `kb_doc_assets`。验证：与 1.1 相同交叉开关 Scenario。
- [x] 1.3 回归：两开关都开时图和表仍都能抽出并入库；两开关都关时行为与 V4 仅文本解析一致。

## 2. DOCX 浮动/粘贴图

绑定 spec：`rag-service/multimodal-ingest`（Scenario：DOCX 浮动或粘贴图在抽图开启时抽出）
参考 design 决策 3。

- [x] 2.1 抽图开关开启时抽出 DOCX 浮动/粘贴/环绕图（嵌入式 + 文档包媒体补漏），同一 blob 只入一份。验证：非 inline 图产生 image 资产。
- [x] 2.2 核对工作区已有 media 补漏与去重测试：已符合 spec 则保留并补齐缺口；**禁止删除或弱化 media 扫描**。

## 3. 资产 chunk 批量索引写入

绑定 spec：`rag-service/multimodal-ingest`（ADDED 资产 chunk 批量索引写入）
参考 design 决策 2。

- [x] 3.1 同一文档资产阶段收集全部待索引文本后一次批量 embedding + 批量双写；表格仍每行一个 chunk。验证：多行表可逐行检索命中，且不再按行串行完整索引往返。
- [x] 3.2 多张图片 OCR 文本同样走批量写入；单张 OCR 失败仍 skip + warning，不阻断正文。验证：多图 OCR chunk 批量写入 Scenario。
- [x] 3.3 批量 embedding/双写失败按既有契约回滚并 A3003。验证：批量写入失败回滚与正文一致。

## 4. 入库阶段结构化耗时日志

绑定 spec：`rag-service/document-ingest`（ADDED 入库阶段结构化耗时日志）
参考 design 决策 4。

- [x] 4.1 parse / 正文索引 / 资产索引三段结构化耗时日志（含文档标识）；无资产时资产阶段记跳过或耗时 0。验证：含资产与无资产两条 Scenario。
- [x] 4.2 确认 `KB_PARSE_TIMEOUT` 仍只约束解析（文本提取/抽取等待），未扩大到 embedding/资产索引。验证：解析超时语义不变。

## 5. Gradio 上传超时文案

绑定 spec：`rag-service/gradio-frontend`（MODIFIED 网页上传：连接失败 vs 读超时）
参考 design 决策 5。

- [x] 5.1 连接失败提示「后端服务暂不可用」；读超时且请求已发出提示入库处理中（刷列表、勿重传），**不得**提示后端不可用。不改 `_UPLOAD_TIMEOUT` 秒数、不扩 UI。
- [x] 5.2 核对工作区已有 ReadTimeout 分支：已符合则保持；缺文案或误把超时当宕机则按 spec 修正；**禁止回退成统一「后端暂不可用」**。

## 6. 工作区对齐与禁回退总检

- [x] 6.1 对照工作区 `asset_extractor.py`（media 补漏）与 `gradio/app.py`（ReadTimeout）相对 delta spec 做一次总检：缺测补测，已有实现不回退。不把 `.env` / `data/assets` 纳入本变更。

## 7. 单资产失败 Session 安全（冒烟补丁）

绑定 spec：`rag-service/multimodal-ingest`（ADDED 资产 chunk 批量索引写入 — Session/单资产容错 + Scenario：OCR 不可用时含图表文档仍成功入库）
参考 design 决策 6。禁止改 archive、禁止削弱批量失败 A3003、禁止回退 Gradio ReadTimeout 文案、不起 OCR 作为前置。

- [x] 7.1 去掉/替换 `process_assets`（或等价路径）在单资产 except 中对共享 Session 的全量 `db.rollback()`；单张 OCR/单表准备失败仅 skip + warning。验证：模拟 OCR 抛错后 Session 中 `kb_documents` 仍在，不因 rollback 消失。
- [x] 7.2 进入资产批量写 `kb_chunks`/`kb_doc_assets` 前保证文档行可被 FK 引用（正文索引成功后显式 commit 或等价边界）。验证：OCR 不可用时含图表文档 → 正文 active + 表格资产/行 chunk 入库成功；不得出现 `kb_chunks_doc_id_fkey` / 仅因 OCR 导致的 B0007。
- [x] 7.3 回归：资产批量 embedding/双写失败仍回滚索引并 A3003（决策 6 第 3 点不削弱）。验证：与任务 3.3 同语义。

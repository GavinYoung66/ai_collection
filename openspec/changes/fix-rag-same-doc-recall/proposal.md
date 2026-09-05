## Why

隔离检索「RAG 系统技术类别有哪些」时，表格行已在 PG/ES 且进入 BM25 top20，却被 `_dedup_by_doc_id`（现行 MUST：同文档只留 1 chunk）砍到只剩标题「3.1 RAG多模态知识库系统」，LLM 上下文无答案。同时 Gradio 多轮空格拼接上一问、表格行无章节名、同名 active 可重复入库，放大该问题。诊断已完成，现在用规范修正检索契约，禁止靠调 rerank/prompt 蒙混。

## What Changes

- **P0 同文档多 chunk（BREAKING 相对旧 MUST）**：RRF 后按 doc_id 保留 RRF 最高 **N** 条（`N = min(top_k, KB_DOC_CHUNK_KEEP)`，默认 keep=5），不再强制 1 条。无「关回只留 1 条」开关。`_dedup_by_content` 保持（不同 doc 相同内容仍只留 1）。
- **P0 表格可定位**：行序列化带最近标题/表名；并写 1 条「列汇总 chunk」（技术类别等整列取值）。A+B 都做。单表失败仍 skip。
- **P1 Gradio 检索 query**：发给 `/kb/search` 的 query MUST 为本轮原句，禁止拼接最近 3 轮。
- **P1 同名 active 拒绝**：已有同 title + active + 未删除时 POST MUST 拒绝（B0001）；Gradio 同步提示。processing 同名拒绝保持。不做 content hash。
- **P2**：Gradio processing 同名检测须穷尽列表，禁止只扫 `page_size=100` 第一页；`delete_document` 改为 `await get_embed_client()`（实现债，无检索语义变化）。
- **Non-Goals**：不改 RRF 公式、双闸门阈值、LLM prompt/红线、JWT/行级权限、archive；不做上传自动轮询进度条；禁止把调 rerank/改 prompt 当作修 Q2 的手段。

## Capabilities

### New Capabilities

无。（增量演进既有能力，不新增 capability 目录。）

### Modified Capabilities

- `rag-service/hybrid-search`：RRF 后同 doc_id 保留 top-N，不再「仅最高 1 条」；内容级去重保留。
- `rag-service/search-enhancements`：交叉引用的「同 doc 只留 1 条」MUST 改为 top-N，与 hybrid-search 对齐。
- `rag-service/multimodal-ingest`：表格行带章节上下文 + 列汇总 chunk。
- `rag-service/document-ingest`：同 title + active 未删除时拒绝重复 POST。
- `rag-service/gradio-frontend`：检索不拼接历史 query；active/processing 同名提示；processing 列表防重须翻页穷尽。

## Impact

- **检索行为（BREAKING）**：同一文档可返回多个不同 chunk；`results` 条数与 `fused_count`（doc_id 去重后）相对旧实现上升。Q1 角色定位不得被挤出 top_k。
- **入库**：新文档表格 chunk 文本格式变化；存量需 reindex/重传才带章节名与列汇总（本版不强制批量回填）。
- **上传 API**：同名 active 再 POST 从「新建一行」变为 B0001。
- **配置**：新增 `KB_DOC_CHUNK_KEEP`（默认 5）。
- **代码**：`search_service._dedup_by_doc_id`、`asset_extractor._build_table_asset`、`documents.upload_document`、`doc_repo.find_active_by_title`、`gradio/app.py`（`ask_chat` / `_find_processing_title`）、`delete_document` await。
- **测试**：去重单测旧「只留 1 条」改为 N 条；隔离 curl Q1/Q2 验收。

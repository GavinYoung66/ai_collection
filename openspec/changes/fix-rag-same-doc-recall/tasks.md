# Tasks

实现清单，按依赖排序。绑定 delta specs 与 design 决策。禁止改 RRF 公式、闸门阈值、JWT、archive、LLM prompt；禁止把调 rerank / 改 prompt 当作修 Q2 的手段。

## 1. 配置与同文档 top-N 去重

绑定：hybrid-search / search-enhancements「RRF 融合 / 同 doc top-N」；design 决策 1/2。

- [x] 1.1 增加 `KB_DOC_CHUNK_KEEP`（默认 5）到 settings、`.env.example`、rag-service README，并写明 N = min(top_k, keep)。验证：`test_settings` 或等价断言默认值=5 且可被环境变量覆盖。
- [x] 1.2 将 `_dedup_by_doc_id` 改为每 doc 保留 RRF 最高 N 条（N = min(top_k, KB_DOC_CHUNK_KEEP)），无「关回 1 条」开关；`fused_count` 仍为该步之后、内容去重之前；trace 记录截断前条数。验证：单测「同 doc 3 chunk 且 N≥3 → 保留 3」「8 chunk 且 N=5 → 保留最高 5」；旧「只留 1 条」用例改为新契约后绿。
- [x] 1.3 确认 `_dedup_by_content` 仍在 doc_id 截断之后执行且行为不变。验证：不同 doc_id、content 仅空白/大小写差的既有单测仍绿。

## 2. 表格行章节前缀与列汇总

绑定：multimodal-ingest「内嵌表格结构化解析」；design 决策 3/6。

- [x] 2.1 `_build_table_asset`（及 docx/pdf/pptx 抽取路径）为每行加上最近章节/表名（无则表头或「未命名表」），格式保持 `表格(第N页)[定位]: 列:值`。验证：`test_asset_extractor` / `test_table_extract` 断言行 content 含定位前缀且仍含 `列:值`。
- [x] 2.2 每个成功解析的表额外写 1 条列汇总 chunk（优先「技术类别」「子系统」列，否则第一列的全部非空值 + 同一可定位上下文）；单表失败 skip 不 rollback 整单。验证：单测给定含「技术类别」列的表 → 存在汇总 chunk 且列出各取值；解析失败表不阻断正文。
- [x] 2.3 xlsx 行切块若无章节，用 sheet 名作定位前缀（不改变空行跳过与 A3001/A3002）。验证：xlsx 相关单测覆盖 sheet 名前缀，旧空表失败契约不回归。
- [x] 2.4 README/Gradio 注明：默认 reindex 不重建资产，表格增强需重新上传。验证：文档含该说明。

## 3. 同名 active 拒绝与删除路径 await

绑定：document-ingest「重复上传拒绝同名 active」；design 决策 4。

- [x] 3.1 `DocRepo` 增加 `find_active_by_title`（title + status=active + is_delete=0）；`upload_document` 在 processing 检查之外命中 active 则 B0001，不建后台任务。验证：单测/API 测同名 active POST 不新增行；仅 archived 同名可再传；processing 同名仍拒绝。
- [x] 3.2 `delete_document` 改为 `embed = await get_embed_client()`，删除语义不变。验证：删除路径无 `never awaited`；既有删除单测绿。

## 4. Gradio：不拼接 query + 防重穷尽

绑定：gradio-frontend 全部 ADDED；design 决策 5。

- [x] 4.1 `ask_chat` 发给 `/kb/search` 的 query 仅为本轮原句；调试区可展示历史但不得把拼接串当 api_query。验证：单测或审查 `api_query == query`；文案不再写「已拼接近 N 轮」作为实际请求。
- [x] 4.2 上传前检测同名 active 并阻止/强提示；processing 检测翻页或按 title 穷尽，禁止只扫 page_size=100 第一页。验证：单测/夹具覆盖「processing 不在第一页仍拦」与「active 同名提示」。

## 5. 回归与验收总闸

- [x] 5.1 rag-service 相关单测绿（去重、表格抽取、上传防重、Gradio 拼接）。验证：`pytest` 目标模块通过。
- [x] 5.2 隔离 curl Q1（rewrite=false）仍命中含「角色定位:」的 chunk 且 LLM 能答对。验证：记录 search 响应 chunk_index/content 与 answer 摘要。
- [x] 5.3 隔离 curl Q2（rewrite=false、不拼上一问）结果至少 1 条含 `技术类别:` 或 RAG 子系统/列汇总；LLM 能列出至少 3 个技术类别（如多模态解析/语义分块/向量化）。验证：禁止用调 rerank 阈值或改 prompt 完成本条。
- [x] 5.4 同一 Gradio session 先 Q1 再 Q2 后，`kb_search_logs.query` / `query_original` 等于 Q2 原文且不含「角色定位」。验证：查最近两条日志。
- [x] 5.5 对已有 active 同名 docx 再 POST → B0001，active 行数不变。验证：PG 统计同 title active=1。

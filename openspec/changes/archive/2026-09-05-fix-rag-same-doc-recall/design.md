## Context

见 `proposal.md` Why。现网检索链：双路 top20 → RRF（按 chunk_id）→ `_dedup_by_doc_id`（每 doc 1 条）→ `_dedup_by_content` → Rerank → 双闸门 → top_k。隔离 Q2 的表格行在 BM25 top20 第 4 名，但同文档标题 chunk RRF 更高，去重后只剩标题。表格行序列化只有 `表格(第0页): 列:值`，无章节名。Gradio `ask_chat` 把 `prev_queries[-3:]` 空格拼进 query。上传防重只查 processing。

本设计覆盖 rag-service 检索、表格抽取、上传防重与 Gradio 编排，不引入新服务。

## Goals / Non-Goals

**Goals:**

- 把「同 doc 保留条数」从 1 改为可预期的 N，并保持内容级去重。
- 新入库表格行可被章节词召回，且有列汇总 chunk 可答「整列有哪些」。
- Gradio 检索 query 与用户本轮输入一致；同名 active/processing 前端+后端都拦。

**Non-Goals:**

- 不改 RRF 公式、闸门阈值、JWT、archive、LLM prompt。
- 不强制存量文档批量回填；不要求 content hash 防重。
- 不把 `fused_count` 改成新列；用既有字段 + trace step 区分截断前后即可。
- `delete_document` 的 await 仅为实现债，无 API 语义变化。

## Decisions

### 决策 1：N = min(top_k, KB_DOC_CHUNK_KEEP)，默认 keep=5

- **方案**：`_dedup_by_doc_id` 每 doc 保留 RRF 最高 N 条；N = min(请求 top_k, settings.KB_DOC_CHUNK_KEEP)，keep 默认 5。无 bypass 开关。
- **理由**：用户验收要「技术类别」进最终结果，同时 Q1 的角色定位段不得被挤出 top_k=5。N 与 top_k 对齐，避免 keep=20 却只展示 5 条时内部仍重、对外仍丢。
- **替代**：固定只留 3 条——不够灵活；keep 做成「关回 1 条」开关——违反「规范对齐修正、非可选增强」。

### 决策 2：截断时机仍在 RRF 之后、内容去重与 Rerank 之前

- **方案**：顺序不变：RRF → doc_id top-N → 补 content → content 去重 → 加权 → Rerank。`fused_count` 仍表示 doc_id 截断后、content 去重前。另打 trace step 记录截断前条数。
- **理由**：与现网日志可比；内容去重仍抑制重复文档副本。
- **替代**：Rerank 后再按 doc 截断——会改变闸门输入，超出本版。

### 决策 3：表格 A+B（行前缀章节 + 列汇总 chunk）

- **方案**：`_build_table_asset`（及等价路径）为每行加最近标题/表名；每个表再写 1 条汇总 chunk（可定位上下文 + 标识列全部非空值，优先「技术类别」「子系统」列，否则第一列）。xlsx 行切块若走独立解析器，对「列:值」行施加同一前缀规则（无章节则用 sheet 名）。
- **理由**：只做 A 仍需多行才能答整列；只做 B 则单行召回弱。诊断中 chunk#75 已接近 B。
- **替代**：入库后离线汇总任务——增加作业面，本版不采用。
- **容错**：单表失败 skip，不 rollback 整单（既有 multimodal 契约）。

### 决策 4：同名 active 直接拒绝（B0001），不做确认流

- **方案**：`find_active_by_title` 与现有 `find_processing_by_title` 对称（title + status + is_delete=0）。命中则 B0001。archived 同名允许再传。
- **理由**：与 processing 防重一致、无新 API。提示词选定「拒绝」而非「需确认」。
- **替代**：覆盖写同一 doc_id——会破坏溯源与 version，Non-Goal。

### 决策 5：Gradio 检索不再拼接；防重翻页或按 title 查

- **方案**：`ask_chat` 的 `api_query = query`。`prev_queries` 可留作 UI。processing/active 检测：循环翻页或请求后端按 title+status 过滤直到穷尽，禁止单次 page_size=100。
- **理由**：拼接是 P1 放大项，不是 Q2 主因，但必须去掉以免验收被污染。
- **替代**：拼接串只给 rewrite 服务——本版不改 rewrite。

### 决策 6：存量不自动回填

- **方案**：新上传走新序列化。已 active 的催收七维文档需用户删除后重传，或后续单独任务。默认 reindex 仍「仅正文、不重建资产」，故 **reindex 不会补章节前缀/列汇总**；文档与 Gradio 须写明「要表格增强请重新上传」。
- **理由**：与 `async-kb-ingest-observability` 的 reindex 语义一致，避免偷偷丢图。

## Risks / Trade-offs

- **[Risk] 同文档 N 条进入 Rerank，标题仍可能排第一** → Mitigation：P0 表格前缀 + 列汇总提高「技术类别」相关 chunk 的 RRF/rerank；验收要求最终 top_k **至少 1 条**含 `技术类别:` 或汇总，不要求标题出局。
- **[Risk] 多 chunk 把角色定位挤出 top_k** → Mitigation：N 受 top_k 限制；Q1 作为回归闸门。
- **[Risk] 列汇总过长超分块上限** → Mitigation：按列截断到合理长度（实现时复用既有 chunk 长度策略），至少保留标识列取值。
- **[Risk] 同名 active 拒绝影响「更新文档」工作流** → Mitigation：先删后传；文案写明。不做覆盖写。
- **[Risk] 未归档的 async-kb-ingest-observability 与本 change 同时存在** → Mitigation：本 change 的 active 拒绝是 ADDED，不改 processing 条款原文；实现时两处检查并列。

## Migration Plan

1. 先合规范，再实现检索 top-N（行为 BREAKING，但请求方仍用同一 `/kb/search`）。
2. 配置 `KB_DOC_CHUNK_KEEP=5` 写入 settings / `.env.example` / rag-service README。
3. 表格增强随新上传生效；存量文档不迁移。
4. 回滚：回退代码即可回到「每 doc 1 chunk」；已按新格式入库的表格行前缀可保留（只多字段，旧检索仍能命中 `列:值`）。

## Open Questions

无（N 的公式、active 拒绝、A+B 均已在提示词中选定）。

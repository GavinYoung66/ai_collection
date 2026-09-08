## MODIFIED Requirements

### Requirement: RRF 融合

系统 MUST 使用 RRF（Reciprocal Rank Fusion）算法融合双路结果：score = 1/(k + rank_dense) + 1/(k + rank_sparse)，k 默认 60，按 chunk_id 去重保留高分。完成后 MUST 再按 doc_id 分组，对每个 doc_id 保留 RRF score 最高的 **N** 个 chunk（N = min(请求 top_k, KB_DOC_CHUNK_KEEP)，KB_DOC_CHUNK_KEEP 默认 5，且 N ≥ 1）。同文档其余 chunk MUST 从候选集剔除。本规则强制生效，MUST NOT 提供「关回每文档只留 1 条」的开关。

**复合去重扩展（V5 修复，本变更不削弱）**：在按 doc_id 保留 top-N 之后，MUST 再执行内容级去重——当两个候选 chunk 的 content 相同（或去除空白/大小写/标点差异后高度相似）时，即使 doc_id 不同，也只保留一份（保留排序靠前/分数最高者）。内容级去重 MUST 不改变 RRF score 计算方式，仅作用于融合结果的输出去重阶段。

检索 trace 或结构化日志 MUST 能区分「按 doc_id 保留 top-N 之前」与「之后」的候选条数（不要求新增 kb_search_logs 列；`fused_count` 语义保持为 doc_id 截断之后、内容去重之前的条数，与现网一致即可）。

本 Requirement **禁止**通过调整 rerank 阈值、相似度闸门或 LLM prompt 来替代「同文档保留 N 条」的契约。

#### Scenario: 两路检索有重叠结果

- **WHEN** 同一 chunk 在向量检索排第 2、BM25 检索排第 1
- **THEN** 该 chunk 的 RRF score = 1/(60+2) + 1/(60+1) ≈ 0.0337，排名上升

#### Scenario: 同一文档 3 个 chunk 进候选集 → 仅保留最高分

- **GIVEN** KB_DOC_CHUNK_KEEP=5 且请求 top_k=5（N=5）
- **WHEN** 同一 doc_id 的 chunk#3（RRF 0.025）、chunk#8（0.018）、chunk#12（0.009）均在融合候选集中
- **THEN** 三条均保留进入 Rerank（3 ≤ N，不再只留最高 1 条）；其他 doc_id 不受影响

#### Scenario: 同一文档多个 chunk 进候选集 → 保留 top-N

- **GIVEN** KB_DOC_CHUNK_KEEP=5 且请求 top_k=5
- **WHEN** 同一 doc_id 的 3 个 chunk RRF 分别为 0.025 / 0.018 / 0.009，另有其他 doc_id 的 chunk
- **THEN** 该 doc_id 的 3 个 chunk 全部保留进入后续 Rerank（3 ≤ N）；其他 doc_id 不受影响

#### Scenario: 同文档候选超过 N 时截断为最高 N 条

- **GIVEN** KB_DOC_CHUNK_KEEP=5 且请求 top_k=5（N=5）
- **WHEN** 同一 doc_id 有 12 个不同 content 的 chunk 进入融合候选
- **THEN** 仅 RRF 最高的 5 条进入 Rerank，其余同文档 chunk 被剔除

#### Scenario: 单 chunk 文档不做剔除（向后兼容）

- **WHEN** RRF 融合后候选集中每条 doc_id 均唯一
- **THEN** 按 doc_id 截断后数量不变，排序与 RRF 输出一致

#### Scenario: 列问答不得只留下标题 chunk

- **GIVEN** 知识库存在文档，其 chunk#14 仅为章节标题「3.1 RAG多模态知识库系统」，chunk#25 起为 `技术类别:` 表格行，且这些表格行已进入双路 top20
- **WHEN** 用户以「RAG多模态全局知识库系统 这个 技术类别 有那些？」检索（rewrite=false，query 不含上一轮问题）
- **THEN** 送入 Rerank / 最终 top_k 的结果 MUST 至少包含 1 条 content 含 `技术类别:` 的 chunk，或含该子系统技术点汇总的 chunk；MUST NOT 仅返回标题 chunk

#### Scenario: 角色定位问答仍命中自洽正文 chunk

- **WHEN** 用户以「RAG多模态全局知识库系统 角色定位是什么」检索（rewrite=false）
- **THEN** 最终结果 MUST 仍包含含「角色定位:」的正文 chunk，不得因同文档多 chunk 保留而将该 chunk 挤出 top_k

#### Scenario: 不同 doc_id 但内容相同 → 仅保留一条

- **GIVEN** 知识库存在 6 个不同 doc_id 的文档，其 chunk content 一字不差
- **WHEN** 用户检索并返回 top_k=6
- **THEN** 该内容仅出现 1 条，不再出现 5–6 条内容完全一致的重复项

#### Scenario: 内容仅格式差异 → 视同重复去重

- **GIVEN** 两条 chunk 内容为同一句话，仅空格/大小写/全半角标点不同
- **WHEN** 两条候选均进入融合结果
- **THEN** 内容级去重将其判定为相同，仅保留排序靠前的一条，不重复输出

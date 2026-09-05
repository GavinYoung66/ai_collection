## MODIFIED Requirements

### Requirement: 同 doc_id 多 chunk 只保留最高分（纯规范对齐引用，见 hybrid-search Modified RRF 融合）

系统 MUST 在 RRF 融合（按 chunk_id 去重）之后、送入 Rerank 之前，按 doc_id 分组，对每个 doc_id 保留 RRF score 最高的 **N** 个 chunk（N = min(请求 top_k, KB_DOC_CHUNK_KEEP)，KB_DOC_CHUNK_KEEP 默认 5，N ≥ 1），其余同 doc_id chunk MUST 从候选集剔除。本逻辑与 hybrid-search「RRF 融合」强制收录一致，无「关回每文档只留 1 条」的独立开关。

内容级去重仍在本步骤之后执行，契约见 hybrid-search，本 Requirement 不削弱。

#### Scenario: 同一制度文档 3 个 chunk，仅最高分入 Rerank

- **GIVEN** KB_DOC_CHUNK_KEEP=5 且请求 top_k=5（N=5）
- **WHEN** RRF 融合后，文档 doc_id="DOC001" 产出 3 个 chunk，RRF score 分别为 0.78 / 0.52 / 0.41，另 3 个 chunk 归属其他 doc_id
- **THEN** DOC001 的 3 个 chunk 均保留进入 Rerank（3 ≤ N，不再只留 0.78 那一条）；其他 doc_id chunk 不受影响

#### Scenario: 同一制度文档多个 chunk，保留 top-N 入 Rerank

- **GIVEN** KB_DOC_CHUNK_KEEP=5 且请求 top_k=5
- **WHEN** RRF 融合后，文档 doc_id="DOC001" 产出 3 个 chunk，RRF score 分别为 0.78 / 0.52 / 0.41，另有其他 doc_id 的 chunk
- **THEN** DOC001 的 3 个 chunk 均保留进入 Rerank；其他 doc_id chunk 不受影响

#### Scenario: 超过 N 时只留最高 N 条

- **WHEN** 同一 doc_id 有 8 个候选且 N=5
- **THEN** 仅 RRF 最高的 5 条进入 Rerank，其余 3 条同文档 chunk 被剔除

#### Scenario: 单 chunk 文档不做剔除（向后兼容）

- **WHEN** RRF 融合后候选集 50 条 chunk，每条 doc_id 均唯一（典型长文档策略=每 doc 仅一段）
- **THEN** 去重后数量仍为 50，排序与 RRF 输出完全一致，与 V1 行为等价

#### Scenario: Rerank 输出再受行级权限过滤，同 doc 不二次去重

- **WHEN** 同 doc_id top-N 截断完成 → 送入 Rerank → 行级权限过滤后结果中意外出现 2 条同 doc_id（如未来模块扩展引入）
- **THEN** 去重/截断阶段仅在 RRF→Rerank 之间执行一次；Rerank 后不再把同 doc 压回 1 条，由下游 top_k 截断自然处理（与 hybrid-search 交叉引用保持一致）

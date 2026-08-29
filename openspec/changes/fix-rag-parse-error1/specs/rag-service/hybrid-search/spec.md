## MODIFIED Requirements

### Requirement: RRF 融合

系统 MUST 使用 RRF（Reciprocal Rank Fusion）算法融合双路结果：score = 1/(k + rank_dense) + 1/(k + rank_sparse)，k 默认 60，按 chunk_id 去重保留高分；完成后**再按 doc_id 只保留最高分 chunk**（对齐 step4/02 §四.2），同一文档多块仅保留最佳单块。**复合去重扩展（V5 修复）**：在按 doc_id 去重基础上，MUST 再执行**内容级去重**——当两个候选 chunk 的 content 相同（或去除空白/大小写/标点差异后高度相似）时，即使 doc_id 不同，也只保留一份（保留排序靠前/分数最高者），避免同一内容因多个 doc_id 重复返回 5–6 条。内容级去重 MUST 不改变 RRF score 计算方式，仅作用于融合结果的**输出去重阶段**。

#### Scenario: 两路检索有重叠结果

- **WHEN** 同一 chunk 在向量检索排第 2、BM25 检索排第 1
- **THEN** 该 chunk 的 RRF score = 1/(60+2) + 1/(60+1) ≈ 0.0337，排名上升

#### Scenario: 同一文档 3 个 chunk 进候选集 → 仅保留最高分

- **WHEN** 同一 doc_id 的 chunk#3（RRF 0.025）、chunk#8（0.018）、chunk#12（0.009）均在融合候选集中
- **THEN** 融合阶段只保留 chunk#3（0.025 最高分）送入 Rerank，最终溯源响应中不会出现同一 doc_id 多条结果

#### Scenario: 不同 doc_id 但内容相同 → 仅保留一条

- **GIVEN** 知识库存在 6 个不同 doc_id 的文档，其 chunk content 一字不差（如均为"催收制度: 条款:第一条, 内容:逾期90天可申请协商还款，分期最长60期..."）
- **WHEN** 用户以"逾期90天 协商还款"检索并返回 top_k=6
- **THEN** 检索结果中该内容仅出现 1 条，不再出现 5–6 条内容完全一致的重复项

#### Scenario: 内容仅格式差异 → 视同重复去重

- **GIVEN** 两条 chunk 内容为同一句话，仅空格/大小写/全半角标点不同
- **WHEN** 两条候选均进入融合结果
- **THEN** 内容级去重将其判定为相同，仅保留排序靠前的一条，不重复输出

## ADDED Requirements

### Requirement: 检索结果排序区分度

系统 MUST 保证重排（Rerank）对中文查询产出的 rerank_score 具有区分度：不得因查询切词失败而令所有候选 rerank_score 恒等（如恒为 0.8）导致排序退化为融合分原始顺序；最终 top_k 排序 MUST 依据可区分度的相关性分（rerank_score 与既有 weight 的组合）降序，使 Top1 为语义最相关项。对中文查询，Rerank 输入 MUST 能基于字符级/分词级特征区分不同候选的相关程度，且 Top1 命中项 content MUST 包含对应答案/关键语义上下文。排序与去重不得破坏既有双闸门兜底、溯源字段与缓存契约。

#### Scenario: 中文查询 rerank_score 具有区分度

- **GIVEN** 用户以中文 query="催收时段是什么时候到什么时候" 检索，候选集中含语义相关与不相关多个 chunk
- **WHEN** Rerank 精排完成
- **THEN** 各候选 rerank_score 不全相等（存在区分度），最终排序结果 ≠ 简单按融合分原顺序，相关 chunk 排前、无关 chunk 排后

#### Scenario: Top1 命中包含答案上下文

- **GIVEN** 知识库存在含"【催收时段自律规范】每日8:00至22:00"的 chunk
- **WHEN** 用户 query="催收时段是什么时候到什么时候"，top_k=6
- **THEN** Top1 结果 content 包含"8:00"与"22:00"等答案关键语义，检索整体不再呈现"找不到答案"的观感

#### Scenario: 去重后 top_k 不出现同内容多条

- **GIVEN** 库内多个不同 doc_id 的文档 chunk 内容一字不差
- **WHEN** 用户检索并返回 top_k=6
- **THEN** 同一内容在结果集中仅出现 1 条，其余位置由不同内容（或其他文档）填充

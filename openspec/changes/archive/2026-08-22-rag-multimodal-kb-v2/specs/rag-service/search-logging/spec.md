## MODIFIED Requirements

### Requirement: 全链路日志记录
系统 MUST 对每次检索请求记录以下字段到 kb_search_logs 表：trace_id、session_id（str，可空）、query（原始 query）、query_original（复制字段，冗余保留用于兼容）、query_rewritten（改写后 query，可空）、intent_tag（路由意图标签，可空）、category、dense_hit_count（向量召回数）、sparse_hit_count（BM25 召回数）、fused_count（融合后数）、reranked_count（重排后数）、max_rerank_score（最高 rerank 分）、hit_doc_ids（命中文档 ID 列表）、hit_chunk_ids（命中片段 ID 列表）、latency_ms（总耗时）、degraded（VARCHAR '0'/'1'，是否降级）、is_fallback（VARCHAR '0'/'1'，是否触发兜底）、cache_hit（VARCHAR '0'/'1'，是否查询缓存命中）。

#### Scenario: 正常检索日志记录
- **WHEN** 用户执行一次检索并成功返回 3 条结果
- **THEN** kb_search_logs 表新增一条记录，包含 trace_id、query、dense_hit_count、sparse_hit_count、max_rerank_score、latency_ms 等字段

#### Scenario: 兜底场景日志记录
- **WHEN** 检索结果 Top1 rerank_score < 0.35，触发兜底
- **THEN** kb_search_logs 表新增记录，is_fallback='1'（VARCHAR），reranked_count > 0，但返回 results 为空

#### Scenario: Query 改写后写入 query_rewritten 字段
- **WHEN** Query 改写成功，rewritten_query="逾期协商还款" confidence=0.9
- **THEN** kb_search_logs 新增记录：query="逾其协商" query_rewritten="逾期协商还款" intent_tag="negotiate" degraded='0'

#### Scenario: 缓存命中时 cache_hit='1'
- **WHEN** 查询命中 Redis `rag:cache:query:v2:{md5}` 缓存
- **THEN** kb_search_logs 新增记录 cache_hit='1'，latency_ms 通常 < 50ms，保障 step6/02 检索命中率监控指标不失真

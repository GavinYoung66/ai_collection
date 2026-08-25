# RAG Search Logging Specification

## Purpose

记录每次混合检索的全链路数据（query、召回数量、分数、耗时、降级状态、缓存命中、trace_id），落库到 PostgreSQL kb_search_logs 表，支持问题复现和 BadCase 排查。

## Requirements

### Requirement: 全链路日志记录

系统 MUST 对每次检索请求记录以下字段到 kb_search_logs 表：trace_id、session_id（str，可空）、query（原始 query）、query_original（复制字段，冗余保留用于兼容）、query_rewritten（改写后 query，可空）、intent_tag（路由意图标签，可空）、category、dense_hit_count（向量召回数）、sparse_hit_count（BM25 召回数）、fused_count（融合后数）、reranked_count（重排后数）、max_rerank_score（最高 rerank 分）、hit_doc_ids（命中文档 ID 列表）、hit_chunk_ids（命中片段 ID 列表）、latency_ms（总耗时）、degraded（VARCHAR '0'/'1'，是否降级）、is_fallback（VARCHAR '0'/'1'，是否触发兜底）、cache_hit（VARCHAR '0'/'1'，是否查询缓存命中）。**缓存命中分支（api/search.py 缓存命中短路路径）MUST 同样记录 query_original / query_rewritten / intent_tag 字段**。**边界说明（审计 D6）**：缓存命中分支因短路无改写/路由上下文可恢复，`query_rewritten` 与 `intent_tag` 落 NULL（允许空值，符合可空字段契约），仅 `query_original` 记录原 query；非缓存路径正常记录改写/路由结果。不得因缓存短路而缺失这三个字段（NULL 视为已记录）。

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

#### Scenario: 缓存命中时 query_rewritten/intent_tag 落空（对齐代码实际行为）

- **WHEN** 查询命中缓存（缓存短路路径不经过改写/路由，无改写/路由上下文可恢复）
- **THEN** kb_search_logs 记录 cache_hit='1'，query_original=原 query，query_rewritten 与 intent_tag 落 NULL（允许空值），与下方"无改写/路由时三字段落空"场景一致

#### Scenario: 缓存命中且无改写/路由时三字段落空

- **WHEN** 查询命中缓存，且 KB_QUERY_REWRITE_ENABLED=false、KB_ROUTING_ENABLED=false
- **THEN** kb_search_logs 记录 cache_hit='1'，query_rewritten 与 intent_tag 落 NULL（符合可空字段契约），query_original 仍记录原 query

### Requirement: 日志不阻断主流程

系统 SHALL 在日志写入失败时不阻断检索响应，日志写入异常仅记录 warning 级别日志。

#### Scenario: 日志写入失败不影响检索

- **WHEN** kb_search_logs 表写入失败（如 PG 连接超时）
- **THEN** 检索结果正常返回给用户，系统记录 warning 日志

### Requirement: 日志可追溯 trace_id

系统 MUST 在日志记录中包含 trace_id 字段，与检索响应中的 trace_id 一致，支持通过 trace_id 查找完整检索链路。

#### Scenario: 通过 trace_id 查找日志

- **WHEN** 用户反馈某次检索结果有问题，提供响应中的 trace_id
- **THEN** 通过 trace_id 在 kb_search_logs 表可查到该次检索的完整日志记录

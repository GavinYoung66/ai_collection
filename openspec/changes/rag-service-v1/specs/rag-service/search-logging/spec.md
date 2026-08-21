## Purpose

记录每次混合检索的全链路数据（query、召回数量、分数、耗时、降级状态、trace_id），落库到 PostgreSQL kb_search_logs 表，支持问题复现和 BadCase 排查。

## ADDED Requirements

### Requirement: 全链路日志记录

系统 MUST 对每次检索请求记录以下字段到 kb_search_logs 表：trace_id、query、category、dense_hit_count（向量召回数）、sparse_hit_count（BM25 召回数）、fused_count（融合后数）、reranked_count（重排后数）、max_rerank_score（最高 rerank 分）、hit_doc_ids（命中文档 ID 列表）、hit_chunk_ids（命中片段 ID 列表）、latency_ms（总耗时）、degraded（是否降级）、is_fallback（是否触发兜底）。

#### Scenario: 正常检索日志记录

- **WHEN** 用户执行一次检索并成功返回 3 条结果
- **THEN** kb_search_logs 表新增一条记录，包含 trace_id、query、dense_hit_count、sparse_hit_count、max_rerank_score、latency_ms 等字段

#### Scenario: 兜底场景日志记录

- **WHEN** 检索结果 Top1 rerank_score < 0.35，触发兜底
- **THEN** kb_search_logs 表新增记录，is_fallback=true，reranked_count > 0，但返回 results 为空

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

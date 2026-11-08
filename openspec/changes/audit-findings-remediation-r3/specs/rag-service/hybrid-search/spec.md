# Spec Delta

## ADDED Requirements

### Requirement: 缓存键语义完整性与 trace 一致性

查询缓存的 key MUST 覆盖所有影响检索结果的入参（含 query、类目、top_k、作用域、改写开关位等）。语义不同的请求 MUST NOT 共享缓存条目。缓存命中时返回的 `trace_id` MUST 为本次请求生成并与本次日志一致，MUST NOT 返回写入缓存时的陈旧 trace_id。

#### Scenario: 改写开关不同不共享缓存

- **WHEN** 同一 query 分别以 `rewrite=true` 与 `rewrite=false` 检索
- **THEN** 两次请求使用不同缓存键，结果不被错误复用

#### Scenario: 命中缓存的 trace_id 与本次一致

- **WHEN** 请求命中已有缓存条目并返回结果
- **THEN** 响应 `trace_id` 等于本次请求的追踪标识，可在本次日志链路中检索到

#### Scenario: 参数拼接无歧义

- **WHEN** 两组参数（`query="ab", category="c"` 与 `query="abc", category=None`）分别生成缓存键
- **THEN** 两个键不相等，缓存条目互不串扰

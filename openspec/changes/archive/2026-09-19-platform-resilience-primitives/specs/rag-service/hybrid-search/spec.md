## ADDED Requirements

### Requirement: 经 common resilience 零件做限流

rag-service hybrid-search 的限流 MUST 委托 `common.resilience.rate_limit.allow` 实现，而非服务私有 Redis INCR 路径。可观测行为 MUST 保持不变：固定窗口阈值（按桶配置 20/60/200）、用户维 keying（优先 `X-User-Id` 头，回退客户端 IP）、Redis 不可用静默放行（限流降级不阻断搜索可用性）。HTTP 429 + B0006 响应契约 MUST 与迁移前完全一致。

#### Scenario: 未超限搜索请求被放行

- **WHEN** 用户发搜索请求带 `X-User-Id: user42`，且当前桶计数低于阈值
- **THEN** 请求进入混合检索管道；计数器经 `common.resilience.rate_limit.allow` 递增

#### Scenario: 超限搜索请求返回 429 + B0006

- **WHEN** 用户发搜索请求，桶计数超过阈值（如 20/60/200）
- **THEN** 响应为 HTTP 429，body `{"code":"B0006","message":"请求过于频繁，请稍后再试","data":null,"trace_id":...}`；不调搜索管道

#### Scenario: Redis 不可用不阻断搜索

- **WHEN** 搜索请求到达时 Redis 宕机
- **THEN** `common.resilience.rate_limit.allow` 返回 `True`（静默放行），搜索继续；不因 Redis 故障返回 429 或 500

#### Scenario: 用户维 keying 保持不变

- **WHEN** 搜索请求到达且无 `X-User-Id` 头
- **THEN** 限流 key 回退到客户端 IP，与迁移前用户维行为一致

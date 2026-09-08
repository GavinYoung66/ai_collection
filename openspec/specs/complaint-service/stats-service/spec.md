## Purpose

定义统计服务：7 项核心指标、by_verdict 四值分组、AI-人工一致率告警阈值、热点缓存 TTL 6h。

## Requirements

### Requirement: 统计指标计算

系统 SHALL 在 `app/service/stats_service.py` 计算以下指标：
- 工单处理时长 P95
- 循环重试次数均值
- 证据不足转人工占比
- AI-人工一致率
- MCP 失败率
- LLM 降级率
- 驳回重判率

#### Scenario: 统计查询

- **WHEN** 调用统计接口（`GET /api/v1/complaints/stats` 经 stats_service，非路由自算）
- **THEN** 返回上述全部指标

### Requirement: by_verdict 四值分组

系统 SHALL 按 verdict 四值分组统计：
agent_violation / user_misunderstanding / compliant_but_poor_experience / pending

#### Scenario: 四值分组齐全

- **WHEN** 查询 by_verdict 统计
- **THEN** 返回四个键，无 normal

### Requirement: 一致率告警

系统 SHALL 在 AI-人工一致率连续两周 <80% 时触发告警（写入告警通道，不阻塞主流程）。

#### Scenario: 一致率低于阈值

- **WHEN** 连续两周一致率 75%
- **THEN** 触发告警

### Requirement: 热点缓存

系统 SHALL 对统计结果写入 Redis 热点缓存 `csmp:hotspots:{start}:{end}`，TTL 6h，避免重复计算。

#### Scenario: 缓存命中

- **WHEN** 同一时间范围重复查询统计
- **THEN** 第二次从缓存返回，TTL 6h

## Purpose

定义指标采集服务：自建薄采集器、业务/系统/Agent 三类指标、告警阈值、60s 采集间隔、Mock 时钟可测。

## Requirements

### Requirement: 三类指标采集

系统 SHALL 在 `app/service/metrics_service.py` 采集三类指标（Prometheus + Redis 双通道）：
1. 业务指标：处理时长 P95/P99、AI-人工一致率、MCP 失败率、LLM 降级率、驳回重判率、反思循环平均次数
2. 系统指标：活跃 session 数、队列积压数、PG 连接池使用率、Redis 内存使用率
3. Agent 指标：各节点执行耗时、LLM Token 消耗量、Schema 校验失败次数

#### Scenario: 指标可查询

- **WHEN** 调用 metrics 查询接口
- **THEN** 返回三类指标的当前值

### Requirement: 告警阈值

系统 SHALL 实现以下告警规则：
- AI-人工一致率连续两周 <80% → 告警
- MCP 失败率 >5% → 告警
- LLM 降级率 >10% → 告警
- 队列积压 >500 → 告警

#### Scenario: 阈值触发告警

- **WHEN** MCP 失败率达到 6%
- **THEN** 触发告警事件

### Requirement: 采集间隔与可测性

系统 SHALL 采集间隔 60s；提供 Mock 时钟注入点，便于测试不等待真实时间。

#### Scenario: Mock 时钟驱动采集

- **WHEN** 测试注入 Mock 时钟快进 60s
- **THEN** 触发一次指标采集

### Requirement: 自建薄采集器

系统 SHALL 自建薄指标采集器（因 `common.monitor.metrics` 尚未实现），接口对齐后续 common 版本，可平滑替换；Redis/Prometheus 通道使用可注入客户端（单测禁止真网）。

#### Scenario: 采集器可 import

- **WHEN** import app.service.metrics_service
- **THEN** 可获取指标采集器单例

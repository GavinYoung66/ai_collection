## Purpose

定义样本回流服务：write_session/write_human_review_truth、失败零丢失暂存、AI-人工不一致高价值标记。

## Requirements

### Requirement: 样本回写

系统 SHALL 在 `app/service/backflow_service.py` 实现样本回写：
- `write_session`：全量 state 序列化回写数据中台
- `write_human_review_truth`：回写人工复核真值

#### Scenario: 回写成功

- **WHEN** 工单 resolved/closed
- **THEN** 调用 write_session + write_human_review_truth

### Requirement: 失败不阻塞主流程

系统 SHALL 在回写失败时重试 3 次，仍失败则告警但不阻塞工单主流程（工单仍可 resolved/closed）。

#### Scenario: 回写失败不阻塞

- **WHEN** 回写 3 次均失败
- **THEN** 工单状态仍正常推进，产生告警（错误码 **A5013**），数据入 csmp:backflow:retry

### Requirement: 零丢失暂存

系统 SHALL 在回写失败 3 次后将数据暂存 Redis 队列 `csmp:backflow:retry`，人工介入前数据永不丢失（REQ-CJ-048）。

#### Scenario: 失败数据入暂存队列

- **WHEN** 回写 3 次失败
- **THEN** 数据出现在 csmp:backflow:retry

### Requirement: 高价值样本标记

系统 SHALL 对 AI-人工 verdict 不一致的案例自动标记为高价值样本，优先推送。

#### Scenario: 不一致标记

- **WHEN** AI verdict ≠ 人工 verdict
- **THEN** 样本被标记 high_value=true

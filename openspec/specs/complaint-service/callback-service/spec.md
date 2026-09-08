## Purpose

定义回调服务：session_id+version 幂等、3 次指数退避重试、死信队列 DLQ、每日对账补偿。

## Requirements

### Requirement: 回调幂等

系统 SHALL 在 `app/service/callback_service.py` 实现回调幂等：
- 回调请求携带 session_id + version
- 业务系统可基于 session_id+version 去重
- 同一 session_id+version 不重复推送

#### Scenario: 重复回调幂等

- **WHEN** 同一 session_id+version 回调两次
- **THEN** 业务系统只处理一次（幂等键生效）

### Requirement: 失败重试与指数退避

系统 SHALL 对回调失败实施最多 3 次重试，指数退避 10s / 30s / 60s。

#### Scenario: 三次重试后退避

- **WHEN** 回调连续失败
- **THEN** 分别在 10s、30s、60s 后重试，第 3 次失败入 DLQ

### Requirement: 死信队列

系统 SHALL 将 3 次重试仍失败的回调推入 Redis List 死信队列 `csmp:callback:dlq`（经 `redis_keys.callback_dlq_key`），供人工介入；入 DLQ 不阻塞主流程。

#### Scenario: 入 DLQ

- **WHEN** 回调 3 次重试均失败
- **THEN** 消息出现在 csmp:callback:dlq

### Requirement: 每日对账定时任务

系统 SHALL 提供每日凌晨对账任务，扫描 `draft_generated` 超过 1h 未确认的工单，触发回调补偿（与 session_service 共用定时入口）。

#### Scenario: 对账补偿

- **WHEN** 对账任务发现 draft_generated 超 1h 工单
- **THEN** 重新触发回调

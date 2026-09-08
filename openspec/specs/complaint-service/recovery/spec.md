## Purpose

定义灾备恢复服务：checkpointer 恢复、Redis fallback checkpoint TTL 72h、挂起积压清理、PG 不可用策略。

## Requirements

### Requirement: checkpointer 恢复

系统 SHALL 在 `app/service/recovery_service.py` 实现 LangGraph checkpointer 恢复：恢复成功则继续执行，恢复失败则转 pending_review 人工处理（任务零丢失）。

#### Scenario: 恢复失败转人工

- **WHEN** checkpointer 恢复抛异常
- **THEN** task_status=pending_review，不静默丢失

### Requirement: Redis fallback checkpoint

系统 SHALL 支持 Redis 降级 checkpoint，key `csmp:session:{id}:fallback`（经 `redis_keys.session_fallback_key`），TTL=72h（259200s），读取后恢复 state 并标记 model_degraded。

#### Scenario: Redis fallback 恢复

- **WHEN** PG 不可用但 Redis fallback 存在
- **THEN** 从 fallback 恢复 state，标记 model_degraded=True

### Requirement: 挂起积压清理

系统 SHALL 定期清理挂起工单：task_status 非 archived/closed 且 update_time>24h → pending_review。

#### Scenario: 积压清理

- **WHEN** 工单 pending 超 24h
- **THEN** 转为 pending_review

### Requirement: PG 不可用策略

系统 SHALL 在 PG 主库不可用时：新工单拒绝返回 A5010，已接入工单使用 Redis 降级继续处理。

#### Scenario: PG 不可用新工单拒绝

- **WHEN** PG 不可用且新工单接入
- **THEN** 返回 A5010

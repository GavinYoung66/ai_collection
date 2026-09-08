## ADDED Requirements

### Requirement: 工单接入幂等与 session 创建

系统 SHALL 在 `app/service/session_service.py` 实现工单接入幂等与 session 创建：
- **持久映射** `complaint_id → session_id`：已存在则直接返回已有 session（幂等真相）
- 创建竞态：`SETNX csmp:complaint:lock:{complaint_id}`，**TTL=1800s（30min）**；未抢到则等待/再查映射后返回已有 session
- 创建 complaint_session，初始状态 **`pending`**（非 evidence_gathering）
- 返回 session_id 供后续状态流转
- Key 经 `app.utils.redis_keys.complaint_lock_key` 构造

#### Scenario: 重复接入幂等

- **WHEN** 同一 complaint_id 接入两次（含并发）
- **THEN** 只有一次创建 session，两次返回同一 session_id

#### Scenario: 锁 TTL 为 30min

- **WHEN** 检查 complaint lock TTL 常量
- **THEN** TTL_COMPLAINT_LOCK == 1800

### Requirement: 10 状态流转

系统 SHALL 支持 PRD 6.1 定义的 10 状态流转：
pending → evidence_gathering → reflection_retry → judging → draft_generated → pending_review → pending_senior_review → resolved → closed → archived

#### Scenario: 合法状态流转

- **WHEN** 从 pending_review 执行 approve
- **THEN** 状态变为 resolved

#### Scenario: 创建后为 pending

- **WHEN** 新建 session
- **THEN** task_status == pending

### Requirement: 全局任务超时

系统 SHALL 对处于 judging / draft_generated 超过 30 分钟的工单自动转为 pending_review，避免永久挂起。

#### Scenario: 超时转 pending_review

- **WHEN** 工单处于 judging 超过 30min（MockClock 快进）
- **THEN** 状态自动变为 pending_review

### Requirement: 每日状态对账

系统 SHALL 提供每日对账定时任务，扫描 `draft_generated` 超过 1h 未确认的工单，触发回调/回流重试（与 callback_service 共用 `reconcile.daily_reconcile()`，不重复造两套）。

#### Scenario: 对账扫描

- **WHEN** 对账任务执行
- **THEN** draft_generated 超 1h 工单被列出并触发补偿

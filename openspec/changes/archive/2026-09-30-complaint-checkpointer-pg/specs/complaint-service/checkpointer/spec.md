## Purpose

定义 complaint-service 研判图与会话的 PostgreSQL 持久化能力：PG checkpointer 断点跨进程恢复、终态线程裁剪、多副本图锁互斥，以及 PG 不可用时的降级与可观测性。

## ADDED Requirements

### Requirement: LangGraph Checkpointer PostgreSQL 持久化

`complaint-service` 的 LangGraph 研判图 SHALL 支持以 `AsyncPostgresSaver` 替代进程内 `MemorySaver`，将 `human_review` 断点及图状态持久化到 PostgreSQL，保证进程重启后可恢复。`COMPLAINT_CHECKPOINTER_PG=true` 时 SHALL 使用 `AsyncPostgresSaver` 作为默认 checkpointer，SHALL NOT 静默退回 `MemorySaver` 而不暴露降级状态；`COMPLAINT_CHECKPOINTER_PG` 未开启或 PG 不可用时 SHALL 降级为 `MemorySaver`，降级状态 SHALL 可被健康检查观测。`AsyncPostgresSaver` SHALL 使用 `AsyncConnectionPool` 管理连接，SHALL NOT 使用裸连接。DSN SHALL 从 `COMPLAINT_PG_DSN` 读取，缺省时从 `PG_HOST/PG_PORT/PG_USER/PG_PASSWORD/PG_DB` 派生。

#### Scenario: PG 持久化启用时断点可恢复

- **WHEN** `COMPLAINT_CHECKPOINTER_PG=true` 且 judge 停在 `human_review` 断点
- **THEN** 图状态写入 `checkpoints`/`checkpoint_blobs`/`checkpoint_writes` 三表，服务重启后新进程 `aget_state(thread_id)` 能恢复同一断点

#### Scenario: PG 不可用时自动降级

- **WHEN** `COMPLAINT_CHECKPOINTER_PG=true` 但 PG 连接失败（网络/迁移未执行）
- **THEN** `init_checkpointer()` 自动降级 `MemorySaver`，`is_checkpointer_degraded()` 返回 true，`/health` 暴露降级标记，服务 SHALL NOT 崩溃

#### Scenario: 默认不开启（dev 环境兼容）

- **WHEN** `COMPLAINT_CHECKPOINTER_PG` 未设置或 false
- **THEN** checkpointer 为进程内 `MemorySaver`，与此前行为一致，SHALL NOT 连接 PostgreSQL

---

### Requirement: Checkpoint 保留与裁剪策略

已终态（`resolved`/`rejected`/`closed`）的研判线程 SHALL 在超期后自动从 checkpoint 三表裁剪，非终态线程 SHALL 保留。裁剪 SHALL 支持 dry-run 模式只返回预估删除计数并返回删除计数。保留窗口默认 90 天，SHALL 可配置。裁剪 SHALL 由 complaint-service 内置维护循环（`maintenance.py`）周期执行；多副本部署时 SHALL 持 `csmp:maintenance:lock` Redis 锁，保证同一时刻只有一个副本执行裁剪。

#### Scenario: 终态超期线程被裁剪

- **WHEN** 线程状态为 `resolved` 且最后更新超保留窗口
- **THEN** `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 中该 `thread_id` 全部删除

#### Scenario: 非终态线程不裁剪

- **WHEN** 线程状态为 `pending` / `judging` / `human_review` 且超期
- **THEN** 保留所有 checkpoint 数据，SHALL NOT 删除

---

### Requirement: 研判会话 PostgreSQL 持久化

complaint-service SHALL 将会话记录持久化到 `complaint_sessions` 表（`aflush_session` / `aload_session` / `aload_by_complaint` / `aflush_graph_snapshot`），进程重启后 SHALL 可从 PG 恢复会话。`COMPLAINT_SESSION_PERSIST=false` 时 SHALL 退回进程内内存；PG 不可用时 SHALL 降级内存并通过 `persist_degraded()` 暴露。图状态真源 SHALL 是 checkpoint 三表，任何人 SHALL NOT 从 `complaint_sessions` 列反推图状态。

#### Scenario: 重启后会话可恢复

- **WHEN** judge 进行中服务重启
- **THEN** 新进程 `aload_session(session_id)` / `aload_by_complaint(complaint_id)` 能从 `complaint_sessions` 恢复会话记录

---

### Requirement: 多副本跨进程图锁

多副本部署时，同一 `complaint_id` 的图执行 SHALL 持 `csmp:complaint:graph_lock:{id}` Redis 锁互斥，SHALL NOT 出现多副本并发跑图导致 checkpoint 分叉。resume 操作 SHALL 持独立的 `csmp:complaint:resume_lock:{id}` 锁，SHALL NOT 与 judge 锁共用（避免互相误判为他人持有）。

#### Scenario: 多副本并发跑图互斥

- **WHEN** 两个副本同时收到同一 complaint_id 的 judge 请求
- **THEN** 仅一个副本持 `graph_lock` 执行图，另一副本等待或返回当前状态，SHALL NOT 并发写 checkpoint

---

### Requirement: `complaint_sessions.task_status` CHECK 约束补 `pending_callback`

`complaint_sessions.task_status` 的 PostgreSQL CHECK 约束 SHALL 包含 `pending_callback` 枚举值，定责流程写库时 SHALL NOT 因枚举缺失而违反约束。

#### Scenario: 定责流程写 pending_callback

- **WHEN** 回调通知写 `task_status='pending_callback'`
- **THEN** 数据库 INSERT/UPDATE 成功，SHALL NOT 报错 "CHECK constraint violated"

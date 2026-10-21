# 设计说明：complaint-checkpointer-pg

> 纯文档补录。实现已落地，本文档描述决策依据与降级语义，供后续维护追溯。

## 上下文

- complaint-service LangGraph 图含 `human_review` 断点，断点状态保存在 checkpointer
- 此前使用进程内 `MemorySaver`：重启丢断点、多实例不可用
- LangGraph 官方提供 `langgraph-checkpoint-postgres`（`AsyncPostgresSaver`），与项目 PostgreSQL 复用同一实例，无新增中间件

## 决策

### D1：AsyncPostgresSaver + AsyncConnectionPool

- 与 SQLAlchemy async engine 共存，不复用其连接池（LangGraph checkpointer 有独立事务语义）
- 池大小由 `COMPLAINT_PG_POOL_MIN/MAX` 控制，默认 min=5/max=20
- DSN 优先级：`COMPLAINT_PG_DSN` > `PG_*` 派生；与 `common/db/session.py` 保持一致

### D2：降级而非失败

- PG 不可用时降级 `MemorySaver` 而不是启动失败：开发/CI 环境 PG 可能未起，强制 PG 会阻塞本地跑图
- 降级状态必须可观测：`/health` 暴露 `checkpointer: memory` + `pg: degraded`，运维可据此判断是否需要告警
- 生产语义：pre.env / prod.env 显式 `COMPLAINT_CHECKPOINTER_PG=true`，部署 checklist 含 `make migrate` 前置；未迁移则降级并暴露给监控

### D3：三表 + 索引的迁移设计

- `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 与 LangGraph 官方 schema 对齐，不自创表结构
- 索引按 `thread_id` 建立，裁剪查询走索引
- `complaint_sessions.task_status` CHECK 约束补 `pending_callback` 与 checkpointer 无功能关联，属同批次发现的约束缺失，合并迁移减少一次 schema 变更

### D4：裁剪走内置维护循环 + 跨副本互斥锁

- `app/service/maintenance.py` 内置维护循环：周期执行 `run_maintenance()`（超时转人工 + 三表裁剪 `prune_checkpoints(retention_days=90)`）
- 多副本安全：维护循环持 `csmp:maintenance:lock` Redis 锁，同一时刻只有一个副本执行
- 非终态线程永不自动裁剪：human_review 停 90 天是合理业务场景，不能误删
- `prune_checkpoints` 支持 dry_run 预演并返回删除计数，ops 也可手动调用

### D5：会话 PG 持久化 + 跨进程图锁

- `session_service.py` 新增 PG 持久化路径：`aflush_session` / `aload_session` / `aload_by_complaint` / `aflush_graph_snapshot`（写 complaint_sessions 表；图状态真源仍是 checkpoint 三表，会话列不反推图状态）
- `COMPLAINT_SESSION_PERSIST` 开关控制（默认 true）；PG 不可用时降级内存并暴露 `persist_degraded()`
- 多副本安全：`csmp:complaint:graph_lock:{id}` 防多副本并发跑图导致 checkpoint 分叉；`csmp:complaint:resume_lock:{id}` 与 judge 锁分离避免互相误判
- `recovery_service.py` 标注 DEBT：旧 `recover_from_checkpoint` 形状与 checkpointer 契约不符未接线，真正恢复入口是 `complaint_service._resume_graph_thread`

## 风险与缓解

| 风险 | 缓解 |
|---|---|
| PG 连接池泄漏 | `AsyncConnectionPool` 上下文管理 + lifespan 关闭 |
| 降级后断点丢失无感知 | `/health` 暴露 `pg: degraded`，监控可告警 |
| 裁剪误删在途线程 | 非终态不裁剪 + dry-run 预演 + 返回删除计数 |
| 多实例并发写 checkpoint | LangGraph 官方实现已处理（基于 thread_id + checkpoint_ns 乐观锁） |

## 不做什么

- 不实现 checkpoint 的 UI 查询/管理界面（运维直接查 PG）
- 不做 checkpoint 加密（图状态不含 PII 明文，PII 在证据组装层已脱敏）
- 不改图结构/节点逻辑（本 change 仅替换 checkpointer 后端）

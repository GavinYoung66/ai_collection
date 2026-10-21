# complaint-checkpointer-pg

## Why

complaint-service 的 LangGraph 研判图此前使用进程内 `MemorySaver` 作为 checkpointer，存在两个生产风险：

1. **进程重启丢状态**：`human_review` 断点停在内存中，服务重启/滚动发布后所有待审会话的 `thread_id` 状态丢失，review approve 无法 resume，工单卡死在 judging。
2. **多实例不可用**：MemorySaver 按进程隔离，负载均衡到另一实例时断点不存在，水平扩展被阻断。

同时 `complaint_sessions.task_status` 的 CHECK 约束缺少 `pending_callback` 枚举值，定责流程写库时会违反约束失败。

本 change 将 checkpointer 切换为 PostgreSQL 持久化（`AsyncPostgresSaver` + 连接池），并补齐 CHECK 约束枚举。

## What Changes

- 新增 `app/graph/checkpointer.py`：`COMPLAINT_CHECKPOINTER_PG=true` 时使用 `AsyncPostgresSaver` + `AsyncConnectionPool`；未开启或 PG 不可用时降级 `MemorySaver`，降级状态可被健康检查观测
- 新增迁移 `0015_langgraph_checkpoint_tables.py`：创建 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 三表及索引；修正 `complaint_sessions.task_status` CHECK 约束补 `pending_callback`
- `config.py` 新增 `COMPLAINT_PG_DSN`、`COMPLAINT_CHECKPOINTER_PG`、`COMPLAINT_PG_POOL_MIN/MAX`、`COMPLAINT_SESSION_PERSIST` 配置项
- `session_service.py` 新增会话 PG 持久化（`aflush_session`/`aload_session`/`aload_by_complaint`/`aflush_graph_snapshot`），PG 不可用时降级内存
- `app/service/maintenance.py`（新）：内置维护循环，周期执行超时转人工 + checkpoint 三表裁剪，多副本持 `csmp:maintenance:lock` 互斥
- `utils/redis_keys.py` 新增跨进程锁键：`graph_lock` / `resume_lock` / `maintenance_lock`（多副本安全）
- `common/config/settings.py` 新增 `pg_dsn_psycopg3`（裸 libpq DSN，供 psycopg_pool 使用）
- `recovery_service.py` 标注 DEBT：旧 `recover_from_checkpoint` 未接线，真正恢复入口为 `_resume_graph_thread`
- `main.py` lifespan 调用 `init_checkpointer()`，将 `checkpointer_kind()` / `is_checkpointer_degraded()` 写入 app state
- `/health` 暴露 `checkpointer: postgres|memory` 与降级标记
- 新增 checkpoint 保留/裁剪策略（终态超期线程三表清空，非终态不裁剪，支持 dry-run，默认 90 天）
- `requirements.runtime.txt` 钉死 `langgraph-checkpoint-postgres==3.1.2`、`psycopg[binary]==3.3.6`、`psycopg-pool==3.3.2`
- pre.env / prod.env 开启 `COMPLAINT_CHECKPOINTER_PG=true`，说明依赖 `make migrate` 前置

## Impact

- **Affected specs**: `complaint-service/checkpointer`（新增 capability）
- **Affected code**: `app/graph/checkpointer.py`（新）、`app/service/maintenance.py`（新）、`config.py`、`main.py`、`api/health.py`、`service/session_service.py`、`service/recovery_service.py`、`utils/redis_keys.py`、`models/complaint_session.py`、`common/config/settings.py`、`migrations/versions/0015_langgraph_checkpoint_tables.py`（新）、`requirements.runtime.txt`、`config/pre.env`、`config/prod.env`、新增/更新集成测试（`test_checkpointer_persistence.py`、`test_checkpoint_retention.py`、`test_maintenance.py`、`test_batch10_coverage.py`）
- **降级保证**：PG 未迁移或不可用时自动降级 MemorySaver，服务可启动但 `/health` 标记降级；生产部署必须先 `make migrate`

## 备注

本 change 为**代码先行、文档补录**：实现已在工作树完成并经集成测试验证（进程重启断点恢复、保留策略），本 change 仅补全 spec 追溯链。

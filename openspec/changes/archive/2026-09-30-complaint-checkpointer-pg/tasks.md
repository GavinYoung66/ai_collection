# Tasks: complaint-checkpointer-pg

> 代码已先行落地，本 change 为文档补录。任务全部勾选完成，验收证据见各任务注记。

## 1. 依赖与配置

- [x] 1.1 requirements.runtime.txt 钉死 `langgraph-checkpoint-postgres==3.1.2` / `psycopg[binary]==3.3.6` / `psycopg-pool==3.3.2`
- [x] 1.2 config.py 新增 `COMPLAINT_PG_DSN` / `COMPLAINT_CHECKPOINTER_PG` / `COMPLAINT_PG_POOL_MIN` / `COMPLAINT_PG_POOL_MAX` / `COMPLAINT_SESSION_PERSIST`
- [x] 1.3 pre.env / prod.env 开启 `COMPLAINT_CHECKPOINTER_PG=true`，注明 `make migrate` 前置依赖

## 2. 数据库迁移

- [x] 2.1 迁移 `0015_langgraph_checkpoint_tables.py` 创建 `checkpoints` / `checkpoint_blobs` / `checkpoint_writes` 三表及索引
- [x] 2.2 同迁移修正 `complaint_sessions.task_status` CHECK 约束，补 `pending_callback` 枚举值

## 3. Checkpointer 实现

- [x] 3.1 `app/graph/checkpointer.py`：DSN 解析（COMPLAINT_PG_DSN 优先，PG_* 派生兜底），未配置返回 None
- [x] 3.2 `COMPLAINT_CHECKPOINTER_PG=true` 时用 `AsyncPostgresSaver` + `AsyncConnectionPool`；失败降级 `MemorySaver` 并关闭连接池
- [x] 3.3 `init_checkpointer()` 在 lifespan 初始化并缓存；`is_checkpointer_degraded()` 区分「期望 PG 但退回 MemorySaver」
- [x] 3.4 `main.py` lifespan 调用 `init_checkpointer()`，将 `checkpointer_kind()` / `is_checkpointer_degraded()` 写入 app state
- [x] 3.5 `/health` 暴露 `checkpointer: postgres|memory` 与 pg 降级状态

## 4. 保留与裁剪策略

- [x] 4.1 checkpoint 保留策略：非终态线程不裁剪；终态超期线程三表清空；支持 dry-run 返回删除计数；默认保留 90 天
- [x] 4.2 `app/service/maintenance.py`（新）内置维护循环：周期 `run_maintenance()`（超时转人工 + `prune_checkpoints(90)`），多副本持 `csmp:maintenance:lock` 互斥

## 4b. 会话持久化与多副本锁

- [x] 4.3 `session_service.py` 新增 `aflush_session`/`aload_session`/`aload_by_complaint`/`aflush_graph_snapshot`，`COMPLAINT_SESSION_PERSIST` 开关（默认 true），PG 不可用降级内存并暴露 `persist_degraded()`
- [x] 4.4 `utils/redis_keys.py` 新增 `graph_lock` / `resume_lock` / `maintenance_lock` 跨进程锁键（防多副本 checkpoint 分叉）
- [x] 4.5 `common/config/settings.py` 新增 `pg_dsn_psycopg3`（裸 libpq DSN，psycopg_pool 专用，区别于 asyncpg/psycopg2 方言）
- [x] 4.6 `recovery_service.py` 标注 DEBT：旧 `recover_from_checkpoint` 未接线，真正恢复入口 `_resume_graph_thread`

## 5. 验收

- [x] 5.1 集成测试 `test_checkpointer_persistence.py`：模拟服务重启后新 saver 仍能 `aget_state` 恢复 `human_review` 断点；校验三表列约束
- [x] 5.2 集成测试 `test_checkpoint_retention.py`：非终态不裁剪 / 终态超期三表清空 / 保留窗口内不删 / dry-run 不删除 / 返回删除计数
- [x] 5.3 集成测试 `test_maintenance.py`：维护轮次裁剪 closed 超 90 天线程的 checkpoints
- [x] 5.4 单测 `test_batch10_coverage.py`：`checkpointer_kind` / `is_checkpointer_degraded` 语义（未配置 DSN 时 kind=memory 且 degraded=False）

## 6. 归档

- [ ] 6.1 人工审查通过后执行 `openspec archive complaint-checkpointer-pg`，同步 delta spec 到 main spec
- [ ] 6.2 分开 commit：mcp-gateway-standalone 归档与 checkpointer 相关代码两次提交

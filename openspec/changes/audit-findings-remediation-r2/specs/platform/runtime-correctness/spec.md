## Purpose

扩展平台级运行时正确性基线（第一期已立「在途任务互斥」「资源句柄生命周期」「时间确定性」等需求）：本轮新增后台任务逃逸、shutdown 释放闭环、时间/数值类型一致性、状态迁移原子性、事件循环非阻塞五类缺陷的约束。共同特征：不改变接口契约，但会改变同一输入下或同一生命周期内的可观察结果（任务静默消失、连接泄漏、清理任务崩溃、分数被覆盖、服务冻结）。

## ADDED Requirements

### Requirement: 后台任务强引用与确定性清理

任何 `asyncio.create_task`（含 `loop.create_task`）派发的后台任务 MUST 被强引用持有，且完成后 MUST 被移除（`add_done_callback` discard）。MUST 使用统一的平台原语（`common.spawn_tracked`）派发，MUST NOT 出现裸 `create_task` 且丢弃返回值的调用点。任务被取消或异常退出时，其占用的对称资源（如引用计数锁）MUST 在 finally 路径释放。

#### Scenario: 文档入库任务不被 GC 回收

- **WHEN** rag-service 上传文档触异步入库（解析→向量→ES→PG 全链路后台任务）
- **THEN** 任务在运行期间被强引用集合持有，完成后从集合移除；文档状态必然从 `processing` 推进，不因 GC 停留在中间态

#### Scenario: 图恢复任务取消后引用计数归零

- **WHEN** complaint-service 的图恢复任务被取消或异常退出
- **THEN** `_resume_lock_refs[complaint_id]` 归零，`_resume_locks`/`_resume_lock_refs` 不随工单数无界增长

### Requirement: shutdown 资源释放闭环

长生命周期资源（共享 `httpx.AsyncClient`、DB engine 连接池、Redis 客户端、网关下游连接池、各 provider HTTP client）MUST 在宿主服务 lifespan shutdown 时显式释放（aclose/dispose）。每个服务 MUST 维护完整 shutdown 资源清单，MUST NOT 出现"只建不释"。关停时对已取消后台任务的 await MUST 显式处理 `asyncio.CancelledError`（BaseException 子类），MUST NOT 以 `except Exception` 捕获导致关停中断、剩余任务不被 await。

#### Scenario: 服务优雅关停后连接归零

- **WHEN** 任一生产服务收到 SIGTERM 完成 lifespan shutdown
- **THEN** 其资源清单内的 AsyncClient/engine/Redis/连接池全部 aclose/dispose，进程持有的对外 TCP 连接不悬挂至超时

#### Scenario: 关停取消不击穿 lifespan

- **WHEN** qc-service / data-platform 的常驻后台任务在 shutdown 中被 cancel
- **THEN** CancelledError 被 `contextlib.suppress` 或显式分支吸收，剩余任务逐一被 await，lifespan 以正常状态退出

### Requirement: 时间与数值类型一致性

时间比较 MUST 在同一类型域内进行（float epoch 对 float，aware datetime 对 aware datetime），MUST NOT 出现 float 与 datetime 的算术/比较（`TypeError` 风险）。naive 时间字符串的解释 MUST 走单一事实源（业务时区 Asia/Shanghai），MUST NOT 各调用点自行 `.timestamp()` 按进程本地时区解释。缺时间戳的记录参与过期判定时 MUST 保守跳过（不删除、不清理），MUST NOT 以默认值（如 0）参与比较。金额参与算术或比较时 MUST 使用 Decimal 语义（`Decimal(str(x))` 归一），MUST NOT 依赖 float 二进制近似值做精确比较。

#### Scenario: 清理任务接入 scheduler 不崩溃

- **WHEN** `cleanup_stale` / `cleanup_archived_90d` 被接入 apscheduler，且 store 中 `updated_at` 为 aware datetime
- **THEN** 清理循环完整执行无 TypeError；缺 `updated_at` 的 archived 记录被保守保留

#### Scenario: naive 命中时间戳按业务时区解释

- **WHEN** 上游下发无时区后缀的 `hit_ts` 字符串，容器时区为 UTC
- **THEN** AC 去重窗口按 Asia/Shanghai 语义判定，与 rule_engine 既有约定一致，不出现 8 小时偏移

#### Scenario: 金额精确比较不受 float 近似影响

- **WHEN** 金额参与算术或精确比较（如 20% 偏差判定、金额求和校验），链路值来自 `float` 序列化（API 响应/跨服务传输）
- **THEN** 参与值先经 `Decimal(str(x))` 归一后再运算，结果与十进制精确语义一致，MUST NOT 因 float 二进制近似产生误判

### Requirement: 状态迁移与写入原子性

单实例状态迁移（如人工复核 REVIEWED）MUST 使用条件更新（`UPDATE ... WHERE status=<前置态>`，受影响行数=0 即拒绝）或行锁，MUST NOT 采用"SELECT 校验 → await 让出 → 内存赋值 → commit"的非原子序列。幂等写入 MUST 使用单语句原子形式（`INSERT ... ON CONFLICT DO NOTHING`）并将 `IntegrityError` 识别为幂等成功，MUST NOT 将并发唯一冲突误报为降级/失败。审计字段（首个处理人、首次 ACK 时间）MUST 幂等保留，MUST NOT 被重复操作覆盖。

#### Scenario: 并发双审只生效一次

- **WHEN** 两个复核请求并发对同一 PENDING_REVIEW 记录提交复核
- **THEN** 恰好一个成功，复核流水仅一条，扣分与最终分数一致；另一个收到 A2007

#### Scenario: 并发会话落库不误报降级

- **WHEN** 两个请求并发对同一 `session_id` 执行 `aensure_persisted`
- **THEN** 两次调用均返回 True，`_persist_degraded` 不被置位

### Requirement: 事件循环非阻塞

async 路径中的重负载同步操作（模型加载、大文件解析、CPU 密集转换）MUST 移入线程池（`asyncio.to_thread`）或进程池执行，MUST NOT 在事件循环线程内同步执行。并发健康检查与业务请求 MUST NOT 因单个请求的初始化工作而停摆。

#### Scenario: 模型加载不冻结服务

- **WHEN** rag-service 首次触发 Whisper 转写，模型初始化耗时数秒
- **THEN** 期间 `/health` 与其他并发请求正常响应（延迟不受模型加载影响）

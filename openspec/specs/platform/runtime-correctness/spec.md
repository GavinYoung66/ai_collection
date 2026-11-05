# platform/runtime-correctness Specification

## Purpose
定义跨服务的运行时正确性基线：同一输入在任意进程、任意重复调用、任意时区环境下 MUST 得到一致且不重复的结果，用于消除「接口契约不变、但并发或容器时区下偶发双跑 / 数据丢失 / 时区偏移」这一类缺陷。

## Requirements

### Requirement: 在途任务互斥与幂等落库

同一工单的图执行 MUST 至多存在一个在途实例。判定与登记 MUST 原子化（"检查是否在途"与"登记为在途"不可分离），MUST NOT 采用 SELECT-then-INSERT 式的 check-then-act。互斥依赖（如 Redis 锁）不可用时 MUST 保守拒绝第二次进入（fail-closed），MUST NOT 退化为双跑。工单落库 MUST 幂等：并发重复请求 MUST 全部返回成功，后到请求按"已存在"处理，MUST NOT 因主键冲突返回落库失败。

#### Scenario: 并发重复触发只跑一次

- **WHEN** 同一 `case_id` 的两个请求在 10ms 内并发进入图执行
- **THEN** 只有一个请求进入状态图，另一个返回"已在执行"；该工单的 checkpoint 序列只有一个

#### Scenario: 互斥依赖不可用时不双跑

- **WHEN** 互斥依赖（Redis）不可用，锁实现处于 fail-open 状态
- **THEN** 第二次进入被拒绝，不产生两个并行的图实例

#### Scenario: 并发落库全部成功

- **WHEN** 两个请求并发写入同一 `case_id` 的工单
- **THEN** 两次调用均返回成功，且数据库中仅存在一条记录

### Requirement: 资源句柄生命周期

文件、图像与网络客户端句柄 MUST 在确定性时点释放。短生命周期读写 MUST NOT 依赖 GC 回收；长生命周期单例（HTTP 客户端、日志 sink）MUST 提供显式关闭入口，并在替换实例时先关闭旧实例。

#### Scenario: 批量摄入不累积句柄

- **WHEN** 连续摄入 100 个音频或图片分片
- **THEN** 进程打开的 fd 数不随批次线性增长，稳定在固定水位

#### Scenario: 长生命周期客户端复用

- **WHEN** 同一服务在 1 分钟内发起 50 次 LLM 调用
- **THEN** 复用同一连接池，不新建 50 个独立客户端

### Requirement: 时间确定性

业务时间判定 MUST 使用带时区的时钟，MUST NOT 使用 naive `datetime.now()` 或已废弃的 `datetime.utcnow()`。外呼时段等合规判定 MUST 显式绑定配置时区，在容器 UTC 环境下 MUST 与业务时区一致。

#### Scenario: 容器 UTC 下外呼时段判定正确

- **WHEN** 容器时区为 UTC，配置 `CALL_TIMEZONE=Asia/Shanghai`，业务时刻为北京时间 10:00
- **THEN** 时段判定结果为"允许外呼"，与本地北京时间 10:00 一致

### Requirement: 确定性灰度路由

灰度分片 MUST 使用跨进程稳定的确定性哈希，同一标识在任意进程、任意重启后 MUST 落入同一分片。MUST NOT 使用受 `PYTHONHASHSEED` 影响的 `hash()`。

#### Scenario: 重启后分片不变

- **WHEN** 同一工单标识在两个不同 `PYTHONHASHSEED` 的进程中分别计算分片
- **THEN** 两次计算得到同一分片

### Requirement: 事件先于落库的补偿

消费方收到事件但本地数据尚不可见时 MUST NOT 静默跳过。MUST 进入可重试或可补偿路径（延迟重试、pending 表 + 定时补偿），且跳过与补偿次数 MUST 可观测。

#### Scenario: 回写早于落库可自愈

- **WHEN** `qc.check.completed` 早于对应质检记录落库到达
- **THEN** 消费方不丢弃该事件，在补偿窗口内重试成功后完成回写

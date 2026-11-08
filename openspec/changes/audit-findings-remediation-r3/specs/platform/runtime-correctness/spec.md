# Spec Delta

## ADDED Requirements

### Requirement: 事件循环非阻塞

任何可能超过 100ms 的同步阻塞操作（子进程调用、模型加载、大文件逐采样计算、同步网络探活）MUST 在事件循环线程之外执行（线程池或异步子进程）。承载 SSE 或长连接的中间件 MUST 使用纯 ASGI 实现，MUST NOT 使用会缓冲响应体的 `BaseHTTPMiddleware`。

#### Scenario: 长音频入库期间健康检查仍可响应

- **WHEN** 一段超过 4 分钟的音频正在执行 ffprobe/ffmpeg 分段与转写
- **THEN** 同进程的 `/health` 响应时间小于 500ms，不出现最长数百秒的整体冻结

#### Scenario: 静音裁剪期间其他请求可响应

- **WHEN** 一次 20MB WAV 正在执行逐采样静音裁剪
- **THEN** 事件循环可继续处理其他请求，裁剪任务可被超时取消

#### Scenario: SSE 流经追踪中间件不被缓冲

- **WHEN** `/mcp` 以 `text/event-stream` 返回多块响应并经过追踪中间件
- **THEN** 客户端逐块收到数据，不出现响应体被聚合后才发送

#### Scenario: 同步探活不冻结事件循环

- **WHEN** 服务内保留或新增的同步网络探活（秒级等待）被执行
- **THEN** 探活在事件循环之外执行或已被移除，其他请求在探活期间仍可响应

### Requirement: 常驻进程内存有界

进程级单例的缓冲与登记表 MUST 具备容量上限或 TTL 清理，MUST NOT 无界 append-only 增长。缓冲 MUST NOT 驻留全量 prompt/response 正文，只允许保留预览或摘要。容量上限 MUST 为显式常量或可配置项。

#### Scenario: 调用日志不随调用次数线性增长

- **WHEN** 同一进程连续执行 10 万次 LLM 调用或审计调用
- **THEN** 对应日志/缓冲长度稳定在上限以内，进程 RSS 不随次数线性增长

#### Scenario: 会话与事件登记表可回收

- **WHEN** 攻击者以随机 `X-Session-Id` 发起 10 万次请求
- **THEN** 会话登记表按 TTL 或容量上限回收，不出现每条请求永久 +1

### Requirement: 事件重投下明细写入幂等

订阅方在 at-least-once 投递语义下 MUST 保证明细写入幂等：已处理完成的事件 MUST 短路，或明细写入以业务唯一键执行 `ON CONFLICT DO NOTHING`。幂等登记 MUST 原子（原子占坑或条件更新），MUST NOT 使用 check-then-act。

#### Scenario: 同一会话结束事件重复投递

- **WHEN** 同一条 `call.session.ended` 被投递两次
- **THEN** 第二次不新增 `qc_violations`/`qc_alerts` 行，告警不重复推送

#### Scenario: 并发同幂等键只执行一次

- **WHEN** 两个请求以同一幂等键并发进入 replay
- **THEN** 只有一个执行，另一个读回同一结果，明细表行数不翻倍

### Requirement: 惰性单例初始化线程安全

惰性单例 getter MUST 使用锁或一次性初始化语义，保证并发首次调用只构造一个实例。并发场景下依赖该单例的并发上限 MUST NOT 因实例分裂而成倍放大。

#### Scenario: 并发首调只构造一个实例

- **WHEN** 100 个并发请求同时首次调用同一惰性单例 getter
- **THEN** 构造函数只被调用一次，所有调用方拿到同一实例

#### Scenario: 并发信号量上限不倍增

- **WHEN** 大量并发检索同时初始化 LLM 服务并获取信号量
- **THEN** 实际并发上限不超过配置值，不随实例数量倍增

### Requirement: 单源收敛完整性

时间解析、URL path 参数白名单与 DSN 构造 MUST 复用单一事实源，MUST NOT 在调用点保留等价本地实现或使用未编码拼接。新增调用点 MUST 使用公共实现。

#### Scenario: 运维传 naive 时间窗口不偏移

- **WHEN** 运维以不带时区的时间字符串调用死信重放窗口
- **THEN** 按业务时区（Asia/Shanghai）解释，不产生 8 小时偏移

#### Scenario: 非法 session_id 不能改写下游路径

- **WHEN** dp 向 qc 转发时 payload 中 `session_id` 为 `x/../y` 或包含 `?`/`#`
- **THEN** 请求被拒绝，不拼接出非预期下游路径

#### Scenario: 密码含保留字符时 DSN 连接正确

- **WHEN** 数据库密码包含 `@ : / # ?` 等保留字符
- **THEN** 生成的连接串正确转义并连接到预期主机，不出现解析错位

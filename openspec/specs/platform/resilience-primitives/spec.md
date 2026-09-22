# platform/resilience-primitives

## Purpose

在 `common` 下提供原子韧性零件（限流、有界并发、有界队列、幂等键、熔断器、可观测钩子、三级超时），供 complaint-service、qc-service、rag-service import 复用，避免各服务私有实现韧性逻辑导致口径不一致。

## Requirements

### Requirement: 固定窗口限流零件

平台 SHALL 在 `common.resilience.rate_limit` 提供固定窗口限流零件，接口 `allow(key: str, limit: int, window: int = 60) -> bool`，递增 Redis 计数器，当 `window` 秒内计数超过 `limit` 时返回 `False`。Redis 不可用时，零件 MUST 静默放行（限流降级不阻断业务可用性）。零件 MUST NOT 阻塞等待 Redis，使用短 socket 超时避免挂死调用方。收到 `False` 的调用方负责抛业务错误（如 B0006），由服务异常处理器转 HTTP 429。

#### Scenario: 未超限请求被放行

- **WHEN** 调用方执行 `allow(key="rag:ratelimit:kb_upload:user42", limit=20, window=60)` 且当前计数为 10
- **THEN** 零件返回 `True`，计数器递增到 11

#### Scenario: 超限请求被拒绝

- **WHEN** 调用方执行 `allow(key="rag:ratelimit:kb_upload:user42", limit=20, window=60)` 且当前计数为 20
- **THEN** 零件返回 `False`，调用方抛 B0006，最终呈现为 HTTP 429

#### Scenario: Redis 不可用不阻断业务

- **WHEN** `allow` 被调用时 Redis 宕机或不可达
- **THEN** 零件返回 `True`（静默放行）并打 warn 日志；不向调用方抛异常

### Requirement: 有界并发零件

平台 SHALL 在 `common.resilience.concurrency` 提供有界并发零件，封装 `asyncio.Semaphore`，当在途数达容量上限时立即返回 `False`（不阻塞）。接口 SHALL 暴露 `async acquire() -> bool` 和 `release()`。

#### Scenario: 有槽位立即获取

- **WHEN** 调用方在 `Semaphore(max_inflight=5)` 上执行 `acquire()`，当前在途 3
- **THEN** 零件返回 `True`，在途数变为 4

#### Scenario: 容量满返回 False 不阻塞

- **WHEN** 调用方在 `Semaphore(max_inflight=5)` 上执行 `acquire()`，当前在途 5
- **THEN** 零件立即返回 `False`，不等待；由调用方决定拒绝动作

#### Scenario: release 释放槽位

- **WHEN** 调用方在 `acquire()` 成功后执行 `release()`
- **THEN** 在途数递减，后续 `acquire()` 可能成功

### Requirement: 有界队列零件

平台 SHALL 在 `common.resilience.queue` 提供有界队列零件，接口 `async push(item) -> bool` 和 `async pop(timeout) -> Optional[item]`。队列达 `max_len` 时，`push` MUST 返回 `False` 而非静默丢数据，由调用方决定拒绝动作。`pop` MUST 在超时后返回 `None`。

#### Scenario: 未满 push 成功

- **WHEN** 调用方在 `BoundedQueue(max_len=100)` 上执行 `push(item)`，当前队列 50
- **THEN** 零件返回 `True`，item 入队

#### Scenario: 队列满 push 返回 False 不静默丢

- **WHEN** 调用方在 `BoundedQueue(max_len=100)` 上执行 `push(item)`，当前队列 100
- **THEN** 零件返回 `False`；item 不被静默丢弃，由调用方决定拒绝动作

#### Scenario: pop 取 item 或超时返回 None

- **WHEN** 调用方在非空队列执行 `pop(timeout=1.0)`
- **THEN** 返回一个 item；空队列在 `timeout` 秒后返回 `None`

### Requirement: 幂等键零件

平台 SHALL 在 `common.resilience.idempotency` 提供幂等键零件，接口 `async acquire(key: str, ttl: int = 1800) -> bool`，用 Redis `SETNX` + TTL。首次获取返回 `True`；同 key 再次获取返回 `False`，防止重复执行或重试双写。Redis 不可用时，零件 MUST 保守拒绝（返回 `False`），而非放行重复执行。

#### Scenario: 首次获取成功

- **WHEN** 调用方首次执行 `acquire(key="complaint:judgment:ticket123", ttl=1800)`
- **THEN** 零件返回 `True`，key 被设置 1800 秒 TTL

#### Scenario: 重复获取被拒

- **WHEN** 调用方在 key 仍存在时执行 `acquire(key="complaint:judgment:ticket123", ttl=1800)`
- **THEN** 零件返回 `False`，阻止重复执行

#### Scenario: Redis 不可用保守拒绝

- **WHEN** `acquire` 被调用时 Redis 宕机
- **THEN** 零件返回 `False`（保守拒绝）并打 warn 日志；阻止而非放行重复执行

### Requirement: 带服务前缀 key 的熔断器零件

平台 SHALL 在 `common.resilience.breaker` 提供熔断器状态机，接口 `class CircuitBreaker(key: str, fail_max: int = 5, reset_timeout: int = 30)`。熔断器 MUST 状态流转：`closed`（正常）→ `open`（连续失败 `fail_max` 次，拒绝所有请求）→ `half-open`（`open` 持续 `reset_timeout` 秒后，只放 1 个探针）→ `closed`（探针成功）或 `open`（探针失败，重置计时）。熔断 key MUST 带服务前缀 + 资源标识，避免一个资源的熔断干掉另一个：LLM key MUST 用 `{svc}:llm_degraded:{model}`，MCP key MUST 用 `{svc}:mcp_degraded:{server}`。禁止用单全局 key（如 `csmp:llm_degraded=1`）干掉全平台 LLM 调用。

#### Scenario: 连续失败打开熔断器

- **WHEN** `CircuitBreaker(key="rag:llm_degraded:deepseek-chat", fail_max=5)` 累计 5 次连续失败
- **THEN** 熔断器转 `open`，后续请求被拒直到 `reset_timeout` 到期

#### Scenario: half-open 只放 1 个探针

- **WHEN** 熔断器 `open` 已持续 `reset_timeout` 秒，有请求到达
- **THEN** 只放 1 个探针请求；并发探针被当作仍 `open` 拒绝

#### Scenario: 探针成功关闭熔断器

- **WHEN** half-open 探针请求成功
- **THEN** 熔断器转 `closed`，恢复正常流量

#### Scenario: 探针失败重开熔断器

- **WHEN** half-open 探针请求失败
- **THEN** 熔断器转回 `open`，重置计时

#### Scenario: 服务前缀 key 隔离失败

- **WHEN** complaint-service 打开 `csmp:mcp_degraded:rag_kb`
- **THEN** qc-service 的 LLM 调用（`qc:llm_degraded:*`）和 rag-service 的 MCP 调用（`rag:mcp_degraded:*`）不受影响；无单 key 干掉全平台

### Requirement: 统一可观测钩子

平台 SHALL 在 `common.resilience.hooks` 提供事件钩子接口 `async def on_event(name: str, payload: dict)`。`name` MUST 至少含两段点分 `{svc}.{action}`（如 `complaint.degraded`），通常三段 `{svc}.{component}.{action}`（如 `csmp.rate_limit.hit`、`csmp.circuit_breaker.open`、`qc.queue.full`）。默认实现 MUST 经 `structlog` 打日志，MUST NOT 依赖外部 publisher 存在。各服务 MAY 用自己的 publisher 替换默认实现（如 complaint-service 发 `complaint.degraded` 事件），但未配置 publisher 时默认 no-op/日志行为 MUST 仍可用。

#### Scenario: 限流命中发钩子事件

- **WHEN** `csmp` 服务的限流检查返回 `False`
- **THEN** 调用 `on_event("csmp.rate_limit.hit", {"key": ..., "limit": ...})` 并经 structlog 打日志

#### Scenario: 熔断器 open 发钩子事件

- **WHEN** 熔断器转 `open`
- **THEN** 调用 `on_event("{svc}.circuit_breaker.open", {"key": ..., "fail_max": ...})`

#### Scenario: 未配 publisher 仍打日志

- **WHEN** 调用 `on_event` 且未注册服务级 publisher
- **THEN** 默认实现经 structlog 打日志，不抛异常

### Requirement: 三级超时配置

平台 SHALL 在 `common.resilience.timeout` 提供超时配置 `@dataclass class Timeout(connect: float = 5.0, read: float = 60.0, total: float = 120.0)`。三级 MUST 表达：建连超时（网络不通快速失败）、读响应超时（下游慢）、总耗时上限（整个调用硬上限）。平台 MUST 约定层级关系：MCP `total=30s` < LLM `total=120s` < 投诉全局 `30min`，使下游超时先于上游触发。

#### Scenario: 建连超时对不可达主机快速失败

- **WHEN** 下游主机不可达，配置 `Timeout(connect=5.0)`
- **THEN** 调用在约 5 秒内失败，而非挂死在 OS 默认超时

#### Scenario: 总超时封顶慢下游

- **WHEN** 下游响应慢，MCP 调用配置 `Timeout(total=30.0)`
- **THEN** 调用在 30 秒被中止，即使读超时（60s）未到，因 total 是硬上限

#### Scenario: MCP total 短于 LLM total

- **WHEN** MCP 调用用 `Timeout(total=30.0)`，LLM 调用用 `Timeout(total=120.0)`
- **THEN** MCP 先超时，等待 MCP 的 LLM 编排不会被单次 MCP 调用阻塞超过 30s

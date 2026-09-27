## Context

当前状态：data-platform 与 qc-service 之间通过 Redis Pub/Sub（库号 3）直接发布/订阅三个业务事件；complaint-service 尚未接入事件总线（EventPublisher 为进程内存 stub）。传输层无持久化、无重试、无 DLQ、无统一契约。三服务各自直接操作 Redis client，无抽象层。RocketMQ 5.x 尚未引入。

约束：
- 项目硬性约束：所有事件必须可审计（structlog）、hooks 命名 `{svc}.{component}.{action}`、环境变量集中根目录 `.env`、Docker 端口显式分配避让。
- 现有 QC 链路（含 `etl.task.done` 按天补检）行为不能变；`USE_MOCK_MCP` 等现有测试不能受影响。
- 用户环境 macOS arm64，需验证官方镜像兼容性。

## Goals / Non-Goals

**Goals:**
1. 交付 `common/eventbus/` 统一抽象层，三服务通过同一接口发布/订阅，底层可切换。
2. 交付 RocketMQ 5.x 本地部署（NameServer+Broker+Proxy），arm64 spike 验证 go/no-go。
3. 现有 QC 链路三事件零语义改动平移到抽象层，`EVENT_BUS=redis` 回归无差异。
4. 统一信封契约 + 白名单强校验 + 消费组命名规范，为下一 change（投诉链事件化）定好契约。
5. RocketMQ 模式提供重试 3 次→DLQ→告警→手动重放完整闭环。

**Non-Goals:**
- 不改任何业务事件 payload、不瘦身 `qc.check.completed`、不做投诉链事件化。
- 不做一键生成接口、不做前端、不入 api-gateway。
- 不删除 Redis Pub/Sub 实现（灰度对照保留）。
- 不做运行时双写。

## Decisions

### Decision 1: 单一物理 Topic + Tag 路由，而非每事件一个 Topic

**选择**：物理 Topic `csmp_business_events`，逻辑事件名（如 `call.session.ended`）放消息 Tag/属性。

**理由**：
- RocketMQ topic 命名规范建议避免大量点号，现有事件名均为点号风格，直接映射为 topic 名不兼容。
- 单 Topic + Tag 是 RocketMQ 官方推荐的事件路由模式，consumer 按 Tag 过滤，运维简单。
- 下一 change 新增事件（`complaint.created`、`complaint.result.ready`）无需新建 Topic，只需注册新 Tag。

**备选**：每事件一个 Topic（如 `call_session_ended`）——被否，Topic 数量随事件膨胀，且消费组订阅关系复杂化。

### Decision 2: 信封契约唯一事实源 = pydantic 模型

**选择**：`common/eventbus/envelope.py` 定义 `EventEnvelope` pydantic 模型，发布/消费双侧强校验。`data` 字段仅允许白名单：`case_id`/`session_id`/`qc_record_id`/`complaint_no`/`backfill_date`。

**理由**：
- 用户偏好"唯一事实源（Single Source of Truth）"；pydantic 模型即代码即文档，三服务 import 同一份。
- 双侧校验防止任何一方绕过契约塞业务数据。
- 与项目现有惯例一致（LangGraph State 也用显式 Pydantic 结构化对象）。

**备选**：JSON Schema 文件 + jsonschema 校验——被否，额外文件维护成本，pydantic 已在全项目使用。

### Decision 3: EventBus 抽象接口 = publish/subscribe 两方法

**选择**：

```
class EventBus(Protocol):
    async def publish(event_name: str, data: dict, *, key: str | None = None) -> None
    async def subscribe(event_name: str, group: str, handler: Callable[[EventEnvelope], Awaitable[None]]) -> None
```

`factory.py` 根据 `EVENT_BUS` 环境变量返回对应适配器实例（进程内单例）。

**理由**：
- 接口最小化，不泄露底层差异；业务代码只需知道 `event_name` 和 `data`。
- `key` 参数用于 RocketMQ 消息 Key（便于按 `session_id` 等查询消息）。
- 三适配器（`redis_pubsub.py` / `rocketmq.py` / `redis_streams.py`）实现同一接口，测试可互换。

**备选**：每个服务自定义抽象——被否，项目约束"禁止各写一套"。

### Decision 4: RocketMQ 消费失败处理 = 框架重试 + 应用层告警

**选择**：依赖 RocketMQ PushConsumer 内建重试机制（consumer group `maxRetryTimes=3`，共投递 4 次），超过后自动入 `%DLQ%{consumer_group}` Topic。应用层在 handler 最后一次失败时发 `csmp.eventbus.dlq` hook + structlog 告警。手动重放脚本 `scripts/event_replay.py` 直接调 RocketMQ Admin API 或 produce 到原 Topic。

**理由**：
- 利用框架内建能力，避免应用层重复造重试/死信队列。
- 告警和重放是应用层职责，RocketMQ 只管投递。

**备选**：应用层自己实现重试+自管理死信表——被否，与 RocketMQ 机制重复，增加不一致风险。

**落地分期**：§2 `rocketmq.py` 先用 SimpleConsumer + 应用层 `MAX_RETRY_TIMES=3` 对齐投递预算（spike Decision 8）。§5.1 闭合方式：handler 耗尽后 **应用层 produce 到 `%DLQ%{consumer_group}`**（与框架 PushConsumer 原生入队等价的可运维结果），并发 `csmp.eventbus.dlq` hook + structlog 告警；不得仅 ack 原消息却不入 DLQ。后续若换 PushConsumer，可改为框架原生 DLQ，接口与重放脚本保持不变。

### Decision 5: Redis 模式不提供 DLQ，能力差异显式写入 spec

**选择**：`redis_pubsub.py` 适配器仅提供进程内重试（最多 3 次，指数退避）+ structlog 告警，不实现持久化 DLQ。

**理由**：
- Redis Pub/Sub 本身无持久化、无消费确认，实现等价 DLQ 需要改用 Redis Streams，成本远超灰度对照价值。
- 用户已冻结决策：Redis 不退役但仅作灰度对照，能力差异明确接受。
- 写入 spec 避免运维误以为 redis 模式有 DLQ 保护。

**备选**：redis 模式也用 Redis Streams 模拟 DLQ——被否，这是 `redis_streams` 适配器的职责，仅在 arm64 spike no-go 时启用。

### Decision 6: arm64 spike 作为 tasks 第一项，go/no-go 分支

**选择**：tasks 第一项为 spike：拉取 `apache/rocketmq:5.3.2` 镜像并启动验证。go → 走 `rocketmq.py` 适配器；no-go → 走 `redis_streams.py` 适配器（消费组+PEL+XCLAIM+死信 stream）。两种路径共享同一 EventBus 接口和信封契约，业务平移任务不受影响。

**理由**：
- arm64 兼容性是唯一运行时风险，必须在最早阶段验证。
- 两种路径下后续任务（QC 链路平移、验收脚本）完全复用，不阻塞。

**备选**：假设 arm64 可用直接实现——被否，风险后置不可控。

### Decision 7: QC 链路平移方式 = 适配器注入 + 信封白名单

**选择**：现有 `publisher.py` / `subscriber.py` 将 Redis `publish()` / `psubscribe()` 替换为 `eventbus.publish()` / `eventbus.subscribe()`。handler 签名改为接收 `EventEnvelope`，从 `data` 取定位字段。线协议 `data` 仅 ID 白名单（与 Decision 1 / event-bus spec 一致）；`qc.check.completed` 的 score/risk 由 data-platform HTTP 反拉，订阅侧 **不** 自动建投诉（质检/投诉链路独立，见 min-loop delta）。

**本 change 上总线的三事件（刻意收窄）**：`call.session.ended`、`etl.task.done`、`qc.check.completed`。qc-service 其余 M1 出站频道（alert/review/system.alert 等）**仍 Redis PUBLISH**，属范围外保留，不是漏迁；投诉链事件化留给下一 change。

**理由**：
- 频道名与开检/回流编排语义保持；线协议升级为信封是本 change 的显式契约，不是「旧 flat JSON 字节级零改动」。
- `EVENT_BUS=redis` 仍作灰度对照（同信封契约 + 无持久化 DLQ）。

**与 tasks「零改动」对齐**：指事件名与定位字段语义；不以旧 Redis 裸 JSON 为回归金标。

### Decision 8: §3 正式 compose / SDK 必须继承 spike 已验证配置（go 路径）

Spike（tasks §1）已在 macOS arm64 上验证通过。§3 把 RocketMQ 写入主 `docker-compose.yml`、以及 `common/eventbus/rocketmq.py` 实现时，**SHALL** 继承下列已验证约束（参考产物：`docker-compose.spike.yml`、`spike-rmq-proxy.json`、`spike-broker.conf`、`spike_loopback.py`、`spike-report.md`）：

1. **Proxy 回广告端口**：宿主机映射 `18081→8081` 时，`rmq-proxy.json` 必须设置 `"useEndpointPortFromRequest": true`。否则 gRPC SDK 会按容器端口 `8081` 回连宿主机导致 `Connection refused`。
2. **Proxy 冷启动**：Proxy 依赖 Broker 就绪后才能创建系统 Topic；compose 中 Proxy 使用 `restart: on-failure`，并对 NameServer/Broker/Proxy 配置 TCP healthcheck；Proxy `depends_on` Broker/NameServer 使用 `condition: service_healthy`。Broker 侧保留 `autoCreateTopicEnable=true`（spike-broker.conf）。
3. **Python Producer**：`rocketmq-python-client` 连接宿主机 Proxy 时，**不要**在构造时预声明 `topics=[...]` 再 `startup()`（会卡在 `_init_settings_event.wait()`）；改为启动后按需发送/拉路由。容器内服务连 `rocketmq-proxy:8081` 时若无此问题可再评估，但默认实现以 spike 安全路径为准。
4. **Python Consumer 订阅**：`SimpleConsumer` / 等价 API 的 subscription 值必须是 `FilterExpression(...)`，禁止传裸字符串（否则 settings 同步 `AttributeError`，表现为 route 拉取失败）。

**备选**：宿主机直接映射 `8081:8081` 而不开 `useEndpointPortFromRequest`——被否，与项目端口避让约定（18081）冲突。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| arm64 镜像不可用 | spike 作为 tasks 第一项，no-go 时降级 redis_streams，契约不变 |
| RocketMQ Proxy 网络配置复杂（宿主机 SDK 连不上 / 端口回广告错误） | spike 已验证：`useEndpointPortFromRequest=true` + 宿主机连 `localhost:18081`；容器内用 `rocketmq-proxy:8081`；详见 Decision 8 |
| Redis 模式无 DLQ，离线丢消息 | 显式写入 spec，运维手册说明 redis 仅作灰度对照；目标模式为 rocketmq |
| 信封强校验误伤存量 payload | 白名单字段覆盖现有三事件全部字段；平移前 diff 校验 |
| 消费组命名冲突 | spec 定义 `gid_{svc}_{event}` 规范，factory 统一拼接，禁止业务自定义 |
| 重试次数语义差异（RocketMQ maxRetryTimes=3 实际投递 4 次） | design 和 spec 均明确"重试 3 次，共投递 4 次"，运维手册同步说明 |

## Migration Plan

1. **前置**：无（纯新增基础设施，不动存量数据）。
2. **部署顺序**：
   - a. spike 验证 arm64（go/no-go）
   - b. 实现 `common/eventbus/`（信封 + 抽象 + 适配器）
   - c. docker-compose 新增 RocketMQ 三组件
   - d. 平移 QC 链路三事件（data-platform publisher → qc-service subscriber → qc-service publisher → data-platform subscriber）
   - e. 冒烟验证 + 回归验证 + DLQ/重放验证
3. **灰度切换**：默认 `EVENT_BUS=redis` 上线，观察一周无异常后切 `rocketmq`；问题秒级回滚（改环境变量重启）。
4. **回滚**：改 `EVENT_BUS=redis` → 重启三服务 → 行为与切换前一致。RocketMQ 容器可保留不删。

## Open Questions

- `scripts/event_replay.py` 的重放实现细节（直接 produce 到原 Topic vs 通过 Admin API 移动 DLQ 消息）——实现时根据 RocketMQ Python SDK 能力选择，不影响 spec 和 tasks。

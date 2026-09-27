## Why

数据中台、合规质检、智能投诉三子系统当前基于 Redis Pub/Sub 通信（无持久化、离线丢消息），且投诉链路存在 HTTP 直推。本项目规划三个顺序 change：`rocketmq-local-bus` → `complaint-event-driven` → `dp-simulation-api`。本 change 为第一个，目标：搭建本地 RocketMQ 5.x + 统一 EventBus 抽象层，并把现有 QC 链路三事件平移到抽象层（payload/编排不变），使 `EVENT_BUS=rocketmq` 切换即刻可用。

## What Changes

1. **本地 RocketMQ 5.x 部署**（docker-compose）：NameServer + Broker + Proxy，宿主机端口避让映射 `19876→9876` / `18080→8080` / `18081→8081`。
2. **统一事件契约**：物理 Topic `csmp_business_events`，逻辑事件名放 Tag；统一信封 `{event_id, event_name, occurred_at, trace_id, data}`，`data` 仅允许 ID 白名单字段（`case_id`/`session_id`/`qc_record_id`/`complaint_no`/`backfill_date`），双侧强校验。
3. **`common/eventbus/` 统一抽象层**：`envelope.py` + `base.py`（publish/subscribe 接口）+ `redis_pubsub.py` + `rocketmq.py` + `factory.py`，三服务共用。
4. **QC 链路三事件平移**：`call.session.ended`、`etl.task.done`、`qc.check.completed` 改走 EventBus 抽象；事件名与定位字段语义不变，信封 `data` 仅白名单；`qc.check.completed` 详情由 DP HTTP 反拉且不自动建投诉；预留 `complaint.created`、`complaint.result.ready` 契约。
5. **消费可靠性**：RocketMQ 模式重试 3 次（共投递 4 次）→ DLQ → 告警日志 → `scripts/event_replay.py` 手动重放；Redis 模式仅作灰度对照（进程内重试+告警，无持久化 DLQ，能力差异写入 spec）。
6. **可观测**：埋点 hooks 遵循 `{svc}.{component}.{action}` 规范（`csmp.eventbus.publish/consume/retry/dlq`），发布消费全程 structlog 审计日志，`event_id`/`trace_id` 贯穿。
7. **冒烟脚本** `scripts/verify_eventbus.py`：两种模式环回验证，可重复执行且无重复消费。

## Capabilities

### New Capabilities
- `platform/event-bus`: 统一事件总线能力——事件信封契约、Topic/Tag/消费组规范、多适配器抽象（Redis Pub/Sub 灰度、RocketMQ 5.x 目标、Redis Streams 降级兜底）、重试/DLQ/告警/重放、可观测埋点。

### Modified Capabilities
- `platform/integration-boundaries`: 异步通知走事件的表述从通用 pub/sub 升级为「统一事件总线」契约（信封 + 适配器 + 白名单 + 重试/DLQ），并明确 MCP/HTTP/EventBus 三者职责边界。
- `data-platform/min-loop`: QC 链路事件的发布/订阅从直接操作 Redis 改为走统一 EventBus 抽象层，对外事件名与 payload 不变。
- `qc-service/events`: 同上——M1 Redis 发布订阅的传输层抽象化，六频道/四订阅/冻结 payload 不变，新增 envelope 强校验与重试/DLQ 语义（仅 rocketmq 模式）。

## Impact

- **代码**：新增 `common/eventbus/`（5 个文件）；`services/data-platform/app/events/publisher.py`、`services/qc-service/app/events/{publisher,subscriber}.py`、`services/data-platform/app/events/subscriber.py` 的 Redis 调用替换为 EventBus 抽象；新增 `scripts/event_replay.py`、`scripts/verify_eventbus.py`。
- **依赖**：`requirements.txt` pin `rocketmq-python-client==5.1.1`。
- **部署**：docker-compose 新增 RocketMQ 5.x（`apache/rocketmq:5.3.2`，NameServer+Broker+Proxy）；根目录 `.env` 新增 `EVENT_BUS` / RocketMQ 端点变量，compose 显式传递。
- **行为**：`EVENT_BUS=redis` 时对外行为与现状完全一致（灰度对照）；`EVENT_BUS=rocketmq` 时获得持久化、消费重试、DLQ 能力。
- **不做**：不改业务 payload、不瘦身 `qc.check.completed`、不做投诉链事件化（`complaint.created` 订阅器、`SIM_AUTO_REVIEW`、删 `JUDGE_BUDGET` 均留给下一 change）、不做一键生成接口/前端/入 api-gateway、不删除 Redis Pub/Sub 实现。

## 1. arm64 Spike（go/no-go 门槛）

- [x] 1.1 拉取 `apache/rocketmq:5.3.2` 镜像，验证 `docker manifest inspect` 支持 `linux/arm64`；输出结论到 spike 报告。验证：manifest 输出含 arm64 或拉取成功
- [x] 1.2 在 macOS arm64 Docker Desktop 上启动 NameServer+Broker+Proxy（最小 compose），验证三组件 running（healthcheck 通过或状态 Up）、端口可连通。验证：`docker compose ps` 三组件 running；`nc -z localhost 19876` 成功
- [x] 1.3 用 Python SDK（`rocketmq-python-client==5.1.1`）从宿主机连接 `localhost:18081`，执行 produce/consume 环回测试。验证：测试消息发送并消费成功
- [x] 1.4 输出 go/no-go 结论：go → 走 `rocketmq.py` 适配器；no-go → 走 `redis_streams.py` 适配器。验证：spike 报告文件 `openspec/changes/rocketmq-local-bus/spike-report.md` 存在，含结论与证据

## 2. common/eventbus/ 统一抽象层

- [x] 2.1 创建 `common/eventbus/envelope.py`：`EventEnvelope` pydantic 模型（`event_id`/`event_name`/`occurred_at`/`trace_id`/`data`），`data` 白名单字段校验（`case_id`/`session_id`/`qc_record_id`/`complaint_no`/`backfill_date`），非白名单字段拒绝并记录 structlog。验证：`pytest common/eventbus/tests/test_envelope.py` 通过（含合法/非法 data 用例）
- [x] 2.2 创建 `common/eventbus/base.py`：`EventBus` Protocol（`publish(event_name, data, *, key=None)` / `subscribe(event_name, group, handler)`）。验证：类型检查通过，三适配器均可实现该 Protocol
- [x] 2.3 创建 `common/eventbus/redis_pubsub.py`：实现 EventBus 接口，底层用现有 Redis client（库号 3）；进程内重试 3 次（指数退避）+ structlog 告警；无持久化 DLQ（spec 已明示）。验证：`EVENT_BUS=redis` 时现有 QC 链路行为与直连 Redis 一致
- [x] 2.4 创建 `common/eventbus/rocketmq.py`（go 路径）或 `common/eventbus/redis_streams.py`（no-go 路径）：实现 EventBus 接口；RocketMQ 模式配置 `maxRetryTimes=3`、消费组命名 `gid_{svc}_{event}`、消息 Key 用 `key` 参数；Redis Streams 模式用消费组+PEL+XCLAIM+死信 stream 模拟。验证：对应适配器的单元测试通过
- [x] 2.5 创建 `common/eventbus/factory.py`：根据 `EVENT_BUS` 环境变量返回对应适配器单例；非法值抛 `ValueError`。验证：`EVENT_BUS=redis`/`rocketmq`/`redis_streams` 分别返回正确适配器；`EVENT_BUS=foo` 抛异常

## 3. RocketMQ 本地部署

- [x] 3.1 docker-compose 新增 RocketMQ 5.x（NameServer+Broker+Proxy），容器内默认端口，宿主机映射 `19876→9876`、`18080→8080`、`18081→8081`；broker 配置允许外部 IP 访问。验证：`docker compose ps` 三组件 healthy；`nc -z localhost 19876` 成功
- [x] 3.2 根目录 `.env` 新增 `EVENT_BUS=redis`、`ROCKETMQ_NAMESRV=rocketmq-namesrv:9876`、`ROCKETMQ_PROXY=rocketmq-proxy:8081`；compose 中三服务显式传递这些变量。验证：`docker compose config` 输出含新变量；三服务容器内 `env | grep EVENT_BUS` 有值
- [x] 3.3 `requirements.txt` pin `rocketmq-python-client==5.1.1`。验证：pin 行存在；在项目 conda 环境 `ai_collection` 中 `pip install 'rocketmq-python-client==5.1.1'` 成功且 `python -c "import rocketmq"` 无报错（全量 `pip install -r requirements.txt` 可能受既有 pymilvus/grpcio 约束影响，不以全量一次装成为本项硬门槛）

## 4. QC 链路三事件平移

> **线协议口径（裁决）**：事件名与业务编排语义不变；上总线的信封 `data` 仅允许 ID 白名单（见 platform/event-bus）。`qc.check.completed` 的 score/risk/grade **不上总线**，由 data-platform `GET /api/v1/qc/records/{session_id}` 反拉。qc 侧 `emitted` 仍可保留完整业务字段供进程内观测。tasks 中「payload 零改动」指事件名与 handler 所需定位字段语义不变，**不是**要求线协议仍为旧 flat JSON。

- [x] 4.1 data-platform `publisher.py`：`call.session.ended` 和 `etl.task.done` 的发布从直接调 Redis client 改为 `eventbus.publish()`；`data` 仅白名单定位字段；`gen_calls_batch` 逐条发 `call.session.ended`。验证：`EVENT_BUS=redis` 时 qc-service 收到事件并正常处理
- [x] 4.2 qc-service `subscriber.py`：订阅逻辑从直接调 Redis `psubscribe` 改为 `eventbus.subscribe()`；handler 函数签名改为接收 `EventEnvelope`，从信封 `data` 提取字段分发到现有处理逻辑。验证：`EVENT_BUS=redis` 时 `call.session.ended` 和 `etl.task.done` 订阅正常触发质检
- [x] 4.3 qc-service `publisher.py`：`qc.check.completed` 的发布从直接调 Redis client 改为 `eventbus.publish()`；构造完整业务 payload 后 `whitelist_data` 上总线。验证：`EVENT_BUS=redis` 时 data-platform 收到事件并更新快照
- [x] 4.4 data-platform `subscriber.py`：订阅逻辑从直接调 Redis `psubscribe` 改为 `eventbus.subscribe()`；handler 从信封提取 `data`；HTTP 反拉质检详情后仅回流快照质检列，**不**自动建投诉。验证：`EVENT_BUS=redis` 时 `qc.check.completed` 订阅正常更新快照质检列

## 5. 消费可靠性与运维脚本

- [x] 5.1 消费端统一重试/DLQ 集成：RocketMQ 模式 handler 最后一次失败时发 `csmp.eventbus.dlq` hook + structlog 告警（含 `event_id`/`event_name`/`consumer_group`/`trace_id`/`last_error`）；Redis 模式进程内重试 3 次后发 `csmp.eventbus.retry` + 告警。验证：人为制造消费失败，rocketmq 模式消息入 DLQ 且有告警日志；redis 模式重试 3 次后有告警日志
- [x] 5.2 创建 `scripts/event_replay.py`：支持按 `event_id` 或时间窗口从 DLQ Topic 读取消息并重投递到原 Topic；`--dry-run` 模式仅打印不重投。验证：先制造 DLQ 消息，执行 `python scripts/event_replay.py --event-id evt_xxx --dry-run` 输出正确；去掉 `--dry-run` 后消息重投递成功
- [x] 5.3 创建 `scripts/verify_eventbus.py`：冒烟脚本，向 `csmp_business_events` 发布测试事件（`verify.test`），订阅方消费后输出结果；支持 `--mode redis|rocketmq` 切换验证。验证：`python scripts/verify_eventbus.py --mode redis` 和 `--mode rocketmq` 均输出 `PASS`；连续两次执行结果一致

## 6. 可观测埋点

- [x] 6.1 EventBus 抽象层集成 hooks：发布时发 `csmp.eventbus.publish`，消费成功发 `csmp.eventbus.consume`，消费重试发 `csmp.eventbus.retry`，死信发 `csmp.eventbus.dlq`；每条含 `event_id`/`event_name`/`trace_id`/`svc`。验证：发布/消费测试事件后，structlog 输出含对应 hook 日志

## 7. 回归与验收

- [x] 7.1 `EVENT_BUS=redis` 模式回归：执行 `run_min_loop.py`，验证 11/11 项通过，行为与改动前一致。验证：脚本输出全部 `PASS`
- [x] 7.2 `EVENT_BUS=rocketmq` 模式回归：执行 `run_min_loop.py`，验证 11/11 项通过，行为与 redis 模式一致。验证：脚本输出全部 `PASS`
- [x] 7.3 DLQ 闭环验收：人为制造消费失败 → 重试 3 次入 DLQ → 告警日志含完整定位信息 → `event_replay.py` 重放成功。验证：每一步日志和结果符合预期
- [x] 7.4 信封强校验验收：构造含非白名单字段的测试消息，验证发布端拒绝并记录日志；直接投递到 Topic 的非法消息被消费端拒绝并记录日志。验证：两端均有拒绝日志
- [x] 7.5 输出验收报告：逐项 pass/fail + 证据（日志片段/命令输出）+ 阻断问题清单（如无则写"无"）。验证：报告文件 `openspec/changes/rocketmq-local-bus/acceptance-report.md` 存在，格式符合用户偏好（逐项对照表）

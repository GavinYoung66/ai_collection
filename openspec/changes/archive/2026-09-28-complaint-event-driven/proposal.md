## Why

`rocketmq-local-bus` 已交付统一 EventBus 与 QC 链路三事件，但投诉链仍由 data-platform/脚本 HTTP 直推三步（建单→judge→review），complaint 事件仍是进程内存 stub，QC 其余出站仍 Redis Pub/Sub，rag 的 `kb.sensitive_updated` 仍 Redis PUBLISH，默认还曾是 `EVENT_BUS=redis`。跨服务异步通知半迁移；主 min-loop 已部分改为「qc.check 只回流质检列」，但仍以 HTTP `POST /complaints` 为投诉触发源并保留 `JUDGE_BUDGET`，与「事件化 + 统一 RocketMQ」目标不一致。本 change 把投诉腿事件化，并把 **dp / qc / complaint / rag** 的跨服务业务事件统一到 RocketMQ，完成三 change 序列的第二步。

## What Changes

1. **总线唯一运行时（BREAKING）**：dp / qc / complaint / **rag** 默认与 compose 强制 `EVENT_BUS=rocketmq`；跨服务业务通知禁止 Redis Pub/Sub。运行时 factory 仅接受 `rocketmq`。Redis 限流/幂等锁/`csmp:callback:dlq` / rag 版本键等非总线用途保留。
2. **QC 六出站 + 四入站上总线**；**rag-service** 将 `kb.sensitive_updated` 从 Redis PUBLISH 改为 EventBus 发布（方法 B：发布方一并迁移）。`strategy.updated`：qc 经 EventBus 订阅；仓内若无发布方则仅保证订阅就绪，未来发布方 MUST 走 EventBus。
3. **投诉链事件化**：DP/脚本发布 `complaint.created`（仅 `case_id` 等白名单）；complaint 订阅后幂等建单 **+** `judge(run_graph=true)`。`SIM_AUTO_REVIEW`（默认 false）。删除 DP `JUDGE_BUDGET`。
4. **双入口语义写死**：事件路径 = 闭环/仿真主编排（create+judge）；HTTP `POST /complaints` = 真业务推单（只建单，不自动 judge，judge 仍走既有 API）。
5. **双轨发布**：跨服务业务 topic → EventBus（白名单 ID）；韧性/观测 hooks（`csmp.*`）→ **不上**业务 Topic，进程内可断言列表 + structlog（对齐 qc）。
6. **回流**：保留 `write_session`；成功后 `complaint.result.ready`；DP 订阅确认。
7. **脚本/契约/compose**：含 `docker-compose.complaint.yml` 等旁路；验收唯一 `EVENT_BUS=rocketmq`。

## Capabilities

### New Capabilities

- （无）

### Modified Capabilities

- `platform/event-bus`: 仅 RocketMQ 运行时；跨服务业务事件范围含 rag；双轨（业务总线 vs hooks）写入契约。
- `platform/integration-boundaries`: MCP / HTTP / EventBus；双入口与双轨。
- `data-platform/min-loop`: `complaint.created` 触发源；去 HTTP 三步主路径与 `JUDGE_BUDGET`；`result.ready`；脚本对齐。
- `qc-service/events`: 全出站/入站经 EventBus（含消费 rag 的 `kb.sensitive_updated`）。
- `complaint-service/events`: stub→EventBus 业务 topic；hooks 双轨；`complaint.created` 订阅；`SIM_AUTO_REVIEW`；`result.ready`；HTTP vs 事件入口。
- `rag-service/sensitive-words-sync`: `kb.sensitive_updated` 改经统一 EventBus 发布，不再 Redis PUBLISH 作跨服务通知。

## Impact

- **代码**：factory 收口；qc 全频道；**rag** `sensitive_words_sync` 改 EventBus；complaint publisher/subscriber；DP 发 created / 订 result.ready；删 `JUDGE_BUDGET`；正文组装迁投诉侧；compose/.env（含 complaint/rag 旁路文件）。另含文档/脚本对齐：`README.md` 事件闭环口径刷新、`scripts/verify_eventbus.py` 删 redis 模式（factory 收口直接后果）、complaint `data_platform_bridge` 增 `persisted` 字段（5.5 result.ready 门槛）、`pytest.ini`+`ai_collection_pytest_plugin.py` 多路径联跑基建（task 1.5）。
- **行为（BREAKING）**：闭环事件化；无 redis 总线运行时；rag 热词通知改 MQ。
- **不做**：`dp-simulation-api`、网关/前端、拆 `write_session`/改图节点、Redis 限流与 callback DLQ List 迁 MQ、为 QC 告警详情新建反拉 API（本 change 只保证投递契约）。

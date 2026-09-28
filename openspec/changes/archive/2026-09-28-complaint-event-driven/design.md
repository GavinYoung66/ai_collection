## Context

See proposal.md — Why。当前：EventBus + RocketMQ 与 QC 三事件已由 `rocketmq-local-bus` 交付；`qc.check.completed` 只回流质检列。投诉仍 HTTP 三步；complaint EventPublisher 为内存 stub（单测依赖 `list_events`）；QC 五出站仍 Redis；**rag** 对 `kb.sensitive_updated` 仍 Redis PUBLISH；`.env` 曾默认 redis。

约束：信封白名单不变（不靠扩白名单塞正文/证据）；MCP/HTTP/EventBus 分层；不改 LangGraph 节点；Redis 限流/幂等/版本键/`csmp:callback:dlq` 保留。

## Goals / Non-Goals

**Goals:**
1. 运行时仅 `EVENT_BUS=rocketmq`；**dp / qc / complaint / rag** 跨服务业务事件经 EventBus。
2. rag 发布 `kb.sensitive_updated` 上总线（方法 B）；qc 订阅同总线。
3. 投诉闭环：`complaint.created` → create+judge → `SIM_AUTO_REVIEW` → `write_session` → `result.ready`。
4. **双轨**：业务 EventBus vs 韧性 hooks 进程内/structlog。
5. **双入口**：事件路径 create+judge；HTTP 路径只建单。
6. 删 `JUDGE_BUDGET`；脚本与旁路 compose 对齐。

**Non-Goals:**
- `dp-simulation-api`、网关、前端、一键生成产品化。
- 用事件替换 `write_session`；改图节点。
- 把 Redis 限流/callback DLQ List 迁到 RocketMQ。
- 为 `qc.alert.raised` 等新建跨服务详情 HTTP API（无订阅方时仅投递+审计）。
- 运行时保留 redis/redis_streams 灰度。

## Decisions

### Decision 1: 运行时 factory 仅接受 `rocketmq`

**选择**：合法值只有 `rocketmq`；否则 `ValueError`。compose / `.env` / `docker-compose.complaint.yml` 等旁路显式传递。`redis_pubsub` / `redis_streams` 移出 factory 注册（文件可留作对照，不得被服务加载）。

**备选**：保留 redis 回滚——被否。

### Decision 2: DP/脚本发 `complaint.created`，投诉只订阅

**选择**：`publish("complaint.created", {case_id})`（`complaint_no` 不预置；与现网 `complaint_id == case_id` 对齐，投诉侧用 `case_id` 作工单号）。消费组 `gid_cs_complaint_created`：幂等 create + judge(run_graph=true)。

**`event_id` 定义**：统一为信封顶层 `event_id` 字段——发布方可显式传入，缺省由 `common/eventbus/envelope.build_envelope` 兜底生成 `evt_<uuid4>`；MQ 消息 ID 不作为幂等依据。消费侧幂等为双层：先按信封 `event_id` 去重（防同条消息重复投递），再按业务定位键（`case_id`）幂等（防不同事件重复建单/双跑图）。`complaint.result.ready` 的防双发同理。

**备选**：投诉自建单后再发 created——被否。

### Decision 3: `complaint_text` 不上总线，由投诉侧组装

**选择**：订阅 handler 按 `case_id` 反查 `qc_violations` / 案件信息，组装差异化正文。将原 DP 侧规则**搬迁或抽到共享纯函数**，并带金标单测，避免漂移。

**备选**：信封带正文——被否（破白名单）。**删除**「短期由 DP 组装」逃生句，避免双主路径。

### Decision 4: `SIM_AUTO_REVIEW` 在投诉进程内 auto-approve

**选择**：默认 false。true 时对 `human_review` interrupt 做等价 `review_action=approve` 的 resume（min-loop 图路径，不是 V4 disposition 多步 UI）。验收环境显式 true。

**触发机制**：复用 judge 既有 resume 守卫——`judge(run_graph=true)` 返回后，在同一进程调用栈内读取该线程图快照，仅当 `next` 明确包含 `human_review` 断点且开关为 true 时才执行 `Command(resume="approve")`；未入断点则跳过，不轮询、不引入额外线程或后台 worker。事件入口与 HTTP judge 入口共用同一封装，开关行为与入口无关。

### Decision 5: 删除 `JUDGE_BUDGET`；流量=事件条数 + 投诉限流

### Decision 6: `write_session` 保留；`complaint.result.ready` 为通知

### Decision 7: `ComplaintClient` 降级为测试/逃生舱

### Decision 8: QC 全频道上总线；alert:stream 等本地 Redis 保留

六出站上总线仅保证投递契约；本 change **不**要求存在跨服务消费者，也 **不**新建 alert 详情反拉 API。有消费者时再按「事件 ID + HTTP 反拉」补读模型。

### Decision 9: rag `kb.sensitive_updated` 上总线（方法 B）

**选择**：`rag-service` 敏感词 version 变更后经 `common/eventbus` 发布 `kb.sensitive_updated`；**SHALL NOT** 再用 Redis PUBLISH 作跨服务通知。信封：`trace_id` / `occurred_at` 走信封顶层；`data` 可为空（`version` 不进 `data`，消费方读既有 Redis 键 `rag:sensitive_words:version` 或直接全量刷新 WordLoader）。发布失败：warning + 不阻断；消费方可依赖 version GET 轮询兜底（既有语义）。rag compose 同样 `EVENT_BUS=rocketmq`。

**`strategy.updated`**：qc 经 EventBus 订阅；仓内无稳定发布方则本 change 只保证订阅路径就绪；任何新增发布方 MUST EventBus，禁止 Redis 旁路主路径。

### Decision 10: 发布双轨（业务总线 vs 韧性 hooks）

| 类型 | 路径 |
|------|------|
| 跨服务业务通知（`complaint.created` / `result.ready` / judged / closed / callback.requested / qc 六出站 / `kb.sensitive_updated` …） | EventBus + 白名单 `data` |
| 韧性/观测 hooks（`csmp.rate_limit.hit` / `csmp.circuit_breaker.open` / `csmp.degrade.triggered` 等） | **不上** `csmp_business_events`；保留进程内可断言列表（或等价 tap）+ structlog；失败不阻断业务 |

单测：业务事件可 mock EventBus；hooks/富字段断言继续打进程内 tap，**不得**要求 hooks 通过白名单信封。

对齐 qc：韧性事件不进六频道业务总线。

### Decision 11: HTTP 建单 vs 事件建单（双入口）

| 入口 | 行为 |
|------|------|
| `complaint.created`（闭环/仿真主路径） | 幂等建单 **+** `judge(run_graph=true)` |
| HTTP `POST /api/v1/complaints`（真业务系统） | **仅建单**；不自动 judge；研判走既有 `POST .../judge` 等 API |

两者共用同一 `create` 幂等语义（同 `case_id` 不双单）。禁止把 HTTP 入口改成隐式自动 judge，也禁止事件路径省略 judge。

### Decision 12: 前置 archive 意识

`rocketmq-local-bus` 已 archive；本 change delta 覆盖投诉事件化、rag 上总线与 RocketMQ 收口。主 min-loop 中残留的「POST 触发 + JUDGE_BUDGET」由本 delta 在 archive 时替换。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 正文组装漂移 | 共享纯函数 + 金标单测（tasks 专条） |
| SIM_AUTO_REVIEW 误开 | 默认 false；仅验收/compose 显式 true |
| factory 拒 redis 打旧脚本 | 全仓扫除 + 旁路 compose |
| hooks 误上总线被白名单拒 | Decision 10 双轨；单测分轨 |
| rag 上总线后 qc 未订到 | 联调任务；version 轮询兜底保留 |
| 六出站无消费者 | 接受；仅投递契约 |
| 误把 HTTP 建成自动 judge | Decision 11 + spec 场景 |

## Migration Plan

1. factory 收口；dp/qc/complaint/**rag** compose 与旁路 yml 强制 rocketmq。
2. rag `kb.sensitive_updated` → EventBus；qc 订阅改总线；QC 六出站上总线。
3. complaint 业务 publisher 上总线 + hooks 双轨；created/result.ready/SIM_AUTO_REVIEW；删 JUDGE_BUDGET；正文搬迁。
4. 脚本与 ComplaintClient 降级；双入口验收。
5. 唯一 `EVENT_BUS=rocketmq` 闭环；残留扫描。
6. 回滚：无 redis 总线回滚；修 MQ / DLQ / git revert。

## Open Questions

- （无）`complaint.created` 仅带 `case_id`（Decision 2 已冻结）。

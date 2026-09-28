## MODIFIED Requirements

### Requirement: 4 频道 + 扩展 topic（单 Publisher）

系统 SHALL **扩展**既有 `app/events/publisher.py` 的 EventPublisher（**禁止**新建 `app/service/event_publisher.py`），对下列**业务**逻辑事件经统一 EventBus（RocketMQ，物理 Topic `csmp_business_events`，Tag=逻辑事件名）发布。SHALL NOT 仅用进程内存列表充当跨服务业务总线。

**PRD 4 频道：**
1. complaint.judged
2. complaint.closed
3. complaint.insight.weekly
4. complaint.mode.switched

**扩展业务 topic（同一 Publisher→EventBus）：**
- complaint.degraded（流程降级告警）
- case.risk.upgraded（停止致电联动）
- callback.requested（V4 回电）
- qc.violation.reported（V4 坐席违规质检通知）
- complaint.result.ready（研判回流成功后的异步通知）

上总线时信封 `data` SHALL 仅含白名单定位字段（`complaint_no` 和/或 `case_id`）；SHALL NOT 在 `data` 中携带投诉正文、证据、PII 或大对象。发布时 MUST 带 `trace_id`（缺失则生成后写入信封）。

**双轨**：韧性/观测 hooks（`csmp.rate_limit.hit`、`csmp.circuit_breaker.open`、`csmp.degrade.triggered` 等）SHALL NOT 写入 `csmp_business_events`；SHALL 保留进程内可断言列表（或等价 tap）+ structlog，失败不阻断。单测对 hooks/富字段的断言 SHALL 打进程内 tap，不得要求 hooks 通过白名单信封上总线。

#### Scenario: 判责完成发布

- **WHEN** 判责完成
- **THEN** `complaint.judged` 经 EventBus 发布，信封含 `trace_id` 与白名单 `data`

#### Scenario: 模式切换发布

- **WHEN** 模式切换成功
- **THEN** `complaint.mode.switched` 经 EventBus 发布

#### Scenario: 降级告警发布

- **WHEN** 流程降级转 pending_review
- **THEN** `complaint.degraded` 经 EventBus 发布（业务 topic，非 `csmp.*` hook）

#### Scenario: 非内存 stub 跨服务

- **WHEN** 发布 `complaint.closed`
- **THEN** 消息进入 `csmp_business_events`，而非仅追加进程内存列表当作跨服务投递

#### Scenario: hooks 不上业务总线

- **WHEN** 限流命中发出 `csmp.rate_limit.hit`
- **THEN** 进程内 tap/structlog 可观测，且该 hook SHALL NOT 作为白名单业务信封写入 `csmp_business_events`

## ADDED Requirements

### Requirement: 订阅 complaint.created 并推进研判

complaint-service SHALL 经统一 EventBus 订阅 `complaint.created`（消费组 `gid_cs_complaint_created`）。收到事件后 SHALL：从信封 `data` 取白名单定位字段（至少 `case_id`；与现网对齐时用工单号=`case_id`，不要求预置 `complaint_no`）；按 `case_id` 反查并组装差异化 `complaint_text`；幂等创建投诉工单；调用既有 judge 路径且 `run_graph=true`，图停在 `human_review`，此时 SHALL NOT 已调用 `write_session`。此为**事件入口**的必经行为（create+judge）。SHALL NOT 改图节点顺序，SHALL NOT 新做路由。重复投递 SHALL 双层幂等：先按信封顶层 `event_id`（发布方显式传入或 `build_envelope` 兜底生成 `evt_<uuid4>`）去重，再按业务定位键（`case_id`）幂等，SHALL NOT 双跑图。MQ 消息 ID SHALL NOT 作为幂等依据。

#### Scenario: 收到 complaint.created 建单并 judge

- **WHEN** complaint-service 收到合法 `complaint.created` 且对应 case 可建单
- **THEN** 落库一张投诉工单并启动 judge(run_graph=true)，停在 human_review

#### Scenario: 重复投递幂等

- **WHEN** 同一 `case_id` 的 `complaint.created` 短时投递两次
- **THEN** 只存在一张工单，不启动第二次完整跑图

### Requirement: HTTP 建单入口不自动 judge

HTTP `POST /api/v1/complaints` SHALL 保持真业务推单语义：成功则建单（同 `case_id` 幂等），SHALL NOT 仅因 create 成功而自动启动 judge。调用方需要研判时 SHALL 使用既有 `POST /api/v1/complaints/{id}/judge` 等 API。事件入口与 HTTP 入口共用 create 幂等，行为在是否自动 judge 上不同。

#### Scenario: HTTP 创建不自动跑图

- **WHEN** 业务系统成功 POST /api/v1/complaints
- **THEN** 工单存在且未仅因该请求进入 judge(run_graph=true)

### Requirement: SIM_AUTO_REVIEW 仿真自动人审

complaint-service SHALL 支持环境变量 `SIM_AUTO_REVIEW`（默认 `false`）。当值为 `true` 且 judge 停在 `human_review` 时，服务 SHALL 在进程内对同一线程执行等价于 `review_action=approve` 的恢复（`Command(resume="approve")`），使图进入既有 `sample_backflow`（min-loop 图路径，不是 V4 disposition 多步 UI 的替代实现）。resume SHALL 复用既有断点守卫：仅当图快照 `next` 明确包含 `human_review` 时才执行，且在 judge 返回后的同一进程调用栈内完成；SHALL NOT 引入额外线程、后台 worker 或轮询等待断点。SHALL NOT 删除或绕过 `human_review` 节点。当值为 `false` 时，SHALL 保持等待真人审；data-platform / 闭环脚本 SHALL NOT 代为 `PUT /review` 作为主路径。

#### Scenario: 开关开启自动 approve

- **WHEN** `SIM_AUTO_REVIEW=true` 且 judge 已停在 human_review
- **THEN** 进程内自动 approve，进入 sample_backflow，随后可调用 write_session

#### Scenario: 开关关闭等人审

- **WHEN** `SIM_AUTO_REVIEW=false` 且 judge 已停在 human_review
- **THEN** 不自动恢复图，等待既有人审 API

### Requirement: 回流成功后发布 complaint.result.ready

complaint-service 在 `sample_backflow` 成功调用 `write_session`（投诉列写入成功）后 SHALL 发布 `complaint.result.ready`，信封 `data` 仅含白名单 ID（至少 `complaint_no` 或与之等价的 `case_id`）。SHALL NOT 用该事件携带 verdict 正文或替代 MCP `write_session` 写快照。`write_session` 失败走既有重试与 `csmp:backflow:retry` 时，SHALL NOT 假装发布成功的 `complaint.result.ready`。

#### Scenario: write_session 成功后发 result.ready

- **WHEN** write_session 成功写快照投诉列
- **THEN** 恰好发布一条 `complaint.result.ready`（幂等：按信封顶层 `event_id` 去重 + `case_id` 业务键幂等，重复成功不刷屏双发）

#### Scenario: write_session 失败不发成功通知

- **WHEN** write_session 连续失败入 backflow retry
- **THEN** 不发布表示成功的 `complaint.result.ready`

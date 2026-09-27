## MODIFIED Requirements

### Requirement: 发布 etl.task.done 触发质检开检

种子入库后，data-platform SHALL 通过统一事件总线（`EVENT_BUS` 适配器）发布已有事件 `etl.task.done`，payload 含 `backfill_date` 等于种子通话结束那天。data-platform SHALL NOT 用 HTTP POST 调用 qc-service。qc-service 已有订阅 `etl.task.done` 的机制，且 `QC_AUTO_COMPLEMENT_AFTER_ETL` 保持默认 true，收到后 GET pending-qc + qc-source 走离线质检。data-platform SHALL NOT 修改 `QC_AUTO_COMPLEMENT_AFTER_ETL` 的默认值。发布后 SHALL 记日志（含 case_id、session_id、backfill_date、发布时间），失败 SHALL 留下可查记录，SHALL NOT 假装成功。

#### Scenario: etl.task.done 发到正确库号

- **WHEN** 种子入库并写快照空行后
- **THEN** `etl.task.done` 发布到统一事件总线 Topic `csmp_business_events`，Tag 为 `etl.task.done`，不发布到 0 号库或其他 channel

#### Scenario: data-platform 不直接调 qc

- **WHEN** data-platform 触发质检
- **THEN** 不发 HTTP POST 到 qc-service，仅通过事件总线发布

#### Scenario: 发布后留下可查记录

- **WHEN** data-platform 发布 etl.task.done
- **THEN** 日志含 case_id、session_id、backfill_date、发布时间；发布失败也留下可查记录

### Requirement: 订阅 qc.check.completed 触发推送投诉

data-platform SHALL 订阅已有事件 `qc.check.completed`（走统一事件总线），收到该事件且确认 `qc_records` 已落库后，仅更新快照质检三列，SHALL NOT 自动创建投诉工单。投诉创建的唯一触发源是显式调用 `POST /api/v1/complaints`（见 min-loop 主 spec 投诉创建需求）。`qc.check.completed` payload 瘦身，信封 `data` 仅含白名单定位字段 `{qc_record_id, session_id, case_id}`；score/risk/grade 等详情由 data-platform 反拉 `GET /api/v1/qc/records/{session_id}` 获取。订阅器 SHALL 处理 payload 中的任意 `case_id`，SHALL NOT 硬编码单个 `SEED_CASE_ID`（如 `11111111-2222-4333-8444-555555555501`）只接受该固定值。同一 `case_id` 的在途/已处理判定 SHALL 保留幂等与在途守卫，重复投递只覆盖、SHALL NOT 产生第二张工单或重复跑图。data-platform SHALL NOT 轮询、SHALL NOT sleep 等待、SHALL NOT 在未收到事件前就推投诉。若 N 分钟（建议 10 分钟）内未收到匹配 case_id 的 `qc.check.completed`，data-platform SHALL 记告警日志（含 case_id、等待时长），SHALL NOT 无限阻塞。投诉创建 SHALL 对每个 case_id 全量执行；judge 与 review SHALL 受有界预算约束（`--judge-budget`，建议约 20 单），仅子集跑图，其余投诉停在"已创建未研判"状态，SHALL NOT 对全量 case_id 跑 judge。质检链路与投诉链路 SHALL 完全独立。

#### Scenario: 收到事件后才推投诉

- **WHEN** data-platform 收到 `qc.check.completed` 且 case_id 匹配任一已入库案件
- **THEN** 仅更新快照质检列；投诉创建由显式 POST /api/v1/complaints 触发

#### Scenario: 未收到事件不推

- **WHEN** data-platform 未收到 `qc.check.completed`
- **THEN** 不更新快照质检列，不调用 POST /api/v1/complaints

#### Scenario: 超时未收到事件记告警

- **WHEN** 种子入库后 N 分钟内未收到匹配 case_id 的 qc.check.completed
- **THEN** 记告警日志（含 case_id、等待时长），不无限阻塞

#### Scenario: 任意 case_id 均被处理

- **WHEN** data-platform 收到 `qc.check.completed` 携带非 `SEED_CASE_ID` 的 case_id
- **THEN** 订阅器对该 case_id 执行质检列回流；投诉创建由显式 POST /api/v1/complaints 触发

#### Scenario: 在途守卫保留

- **WHEN** 同一 case_id 短时间内连续收到两次 `qc.check.completed`
- **THEN** 质检列只覆盖更新，不产生重复回流

#### Scenario: 收到事件后仅回流质检列

- **WHEN** data-platform 收到 `qc.check.completed`
- **THEN** 更新快照质检列；SHALL NOT 调用 POST /api/v1/complaints

#### Scenario: 反拉获取质检详情

- **WHEN** data-platform 收到瘦身后的 `qc.check.completed`
- **THEN** 调用 `GET /api/v1/qc/records/{session_id}` 获取 score/risk/grade

#### Scenario: 质检链路独立

- **WHEN** 未收到 `qc.check.completed`
- **THEN** 不更新快照质检列；SHALL NOT 触发投诉创建

## ADDED Requirements

### Requirement: 发布 call.session.ended 逐条质检

一键生成 30 条通话后，data-platform SHALL 逐条通过统一事件总线发布 `call.session.ended`，每条 payload 仅含 `session_id`。qc-service 订阅后反拉 qc-source 执行质检。data-platform SHALL NOT 批量合并发布。

#### Scenario: 30 条通话逐条发事件

- **WHEN** 一键生成 30 条通话完成
- **THEN** 发布 30 条独立的 `call.session.ended` 事件，每条 payload 仅含 `session_id`

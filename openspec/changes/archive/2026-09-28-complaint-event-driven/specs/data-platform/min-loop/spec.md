## MODIFIED Requirements

### Requirement: 订阅 qc.check.completed 触发推送投诉

data-platform SHALL 订阅已有事件 `qc.check.completed`（走统一 EventBus / RocketMQ），收到该事件且确认 `qc_records` 已落库后，仅更新快照质检三列，SHALL NOT 自动创建投诉工单。闭环/仿真投诉创建的唯一触发源是发布 `complaint.created`（由 data-platform 或脚本经 EventBus 发布）。真业务系统仍可 HTTP `POST /api/v1/complaints`（只建单、不自动 judge，见双入口）。`qc.check.completed` 信封 `data` 仅含白名单定位字段 `{qc_record_id, session_id, case_id}`；score/risk/grade 等详情由 data-platform 反拉 `GET /api/v1/qc/records/{session_id}` 获取。订阅器 SHALL 处理 payload 中的任意 `case_id`，SHALL NOT 硬编码单个 `SEED_CASE_ID`。同一 `case_id` 的质检列更新 SHALL 幂等覆盖。data-platform SHALL NOT 轮询等待投诉完成。若 N 分钟（建议 10 分钟）内未收到匹配 case_id 的 `qc.check.completed`，data-platform SHALL 记告警日志。质检链路与投诉链路 SHALL 完全独立。SHALL NOT 使用 `JUDGE_BUDGET` / `--judge-budget` 在 data-platform 侧编排研判子集（本 delta 替换主 spec 中残留的 budget/HTTP 三步表述）。

#### Scenario: 收到事件后才推投诉

- **WHEN** data-platform 收到 `qc.check.completed` 且 case_id 匹配任一已入库案件
- **THEN** 仅更新快照质检列；投诉创建由后续 `complaint.created` 触发，SHALL NOT 在本事件 handler 内 POST /api/v1/complaints

#### Scenario: 未收到事件不推

- **WHEN** data-platform 未收到 `qc.check.completed`
- **THEN** 不更新快照质检列；闭环脚本 SHALL NOT 把「未质检完成」当作正常捷径直接发 `complaint.created`

#### Scenario: 超时未收到事件记告警

- **WHEN** 种子入库后 N 分钟内未收到匹配 case_id 的 qc.check.completed
- **THEN** 记告警日志（含 case_id、等待时长），不无限阻塞

#### Scenario: 任意 case_id 均被处理

- **WHEN** data-platform 收到 `qc.check.completed` 携带非 `SEED_CASE_ID` 的 case_id
- **THEN** 订阅器对该 case_id 执行质检列回流；SHALL NOT 因 case_id 非固定值而拒绝

#### Scenario: 在途守卫保留

- **WHEN** 同一 case_id 短时间内连续收到两次 `qc.check.completed`
- **THEN** 质检列只覆盖更新，不产生重复异常副作用

#### Scenario: 收到事件后仅回流质检列

- **WHEN** data-platform 收到 `qc.check.completed`
- **THEN** 更新快照质检列；SHALL NOT 调用 POST /api/v1/complaints；SHALL NOT 因该事件自动发布 `complaint.created`

#### Scenario: 反拉获取质检详情

- **WHEN** data-platform 收到瘦身后的 `qc.check.completed`
- **THEN** 调用 `GET /api/v1/qc/records/{session_id}` 获取 score/risk/grade

#### Scenario: 质检链路独立

- **WHEN** 未收到 `qc.check.completed`
- **THEN** 不更新快照质检列；SHALL NOT 触发投诉创建

#### Scenario: 无 JUDGE_BUDGET

- **WHEN** 批量场景需要限制研判数量
- **THEN** 通过少发 `complaint.created` 条数控制，data-platform 配置中不存在生效的 `JUDGE_BUDGET` 编排

### Requirement: 禁止用 qc.complaint.suspected 自动建投诉工单

data-platform SHALL NOT 用 `qc.complaint.suspected` 事件自动新建投诉工单。该事件保持现有语义：只给已经在途的投诉补证。闭环/仿真投诉创建的唯一触发源是 `complaint.created` 事件。

#### Scenario: qc.complaint.suspected 不触发建单

- **WHEN** data-platform 收到 `qc.complaint.suspected` 事件
- **THEN** 不调用 POST /api/v1/complaints，不因此发布 `complaint.created`

### Requirement: 投诉创建后调用 judge 触发研判

闭环主路径下，投诉创建与研判 SHALL 由 complaint-service 订阅 `complaint.created` 后在投诉进程内完成：幂等建单 → `judge` 且 `run_graph=true` → 停在 `human_review`。当 `SIM_AUTO_REVIEW=true` 时，complaint-service SHALL 进程内 auto-approve 进入 `sample_backflow`；当为 `false` 时等待既有人审 API。SHALL NOT 改图节点，SHALL NOT 新做路由。data-platform / 闭环脚本 SHALL NOT 以 HTTP 三步（create→judge→review）作为主编排路径；`ComplaintClient` MAY 保留供单测/逃生舱。HTTP `POST /complaints` 真业务入口只建单、不自动 judge（双入口）。`judge` 的 checkpointer SHALL 按 `complaint_id` 留在投诉进程内直至 review 完成。`run_graph` 只属于 judge 的 `JudgeRequest`。

#### Scenario: 创建后调用 judge 停在人审

- **WHEN** complaint-service 处理 `complaint.created` 完成建单并 judge 且 `SIM_AUTO_REVIEW=false`
- **THEN** 研判停在 human_review，快照投诉列仍为空

#### Scenario: 人审通过后才回流

- **WHEN** 人审 approve（真人审 API 或 `SIM_AUTO_REVIEW=true` 进程内等价 approve）
- **THEN** 同一线程恢复，进入 sample_backflow，并调用 write_session

#### Scenario: 不在创建接口传 run_graph

- **WHEN** 投诉建单（事件订阅 handler 内部 create 或 HTTP POST /api/v1/complaints）
- **THEN** 创建请求体只含 complaint_text 和 case_id，不含 run_graph

#### Scenario: SIM_AUTO_REVIEW 后人审回流

- **WHEN** `SIM_AUTO_REVIEW=true` 且 judge 已停在 human_review
- **THEN** 投诉进程内自动 approve，进入 sample_backflow 并调用 write_session

#### Scenario: 闭环不走 DP HTTP 三步

- **WHEN** 执行 run_min_loop 闭环主路径
- **THEN** data-platform 不通过 ComplaintClient 依次 POST create / judge / PUT review 作为必经步骤

#### Scenario: HTTP 入口不自动 judge

- **WHEN** 业务系统成功 POST /api/v1/complaints
- **THEN** 工单存在且未仅因该 create 自动进入 judge(run_graph=true)

### Requirement: 投诉创建只传 complaint_text 和 case_id

complaint-service 建单时请求体语义 SHALL 只含 `complaint_text` 与 `case_id`。`complaint_text` SHALL 按该案件实际命中的违规项组装差异化正文：违规项 SHALL 由该 session_id 在 `qc_violations` 中的违规记录（含 `speaker`/`redline_tag`）派生（坐席违规→`agent_violation` 类正文，客户侧异常→`user_misunderstanding` 类正文，干净合规→`compliant` 类正文）。闭环主路径下正文由投诉侧在消费 `complaint.created` 后按 `case_id` 反查组装（规则从原 DP 侧搬迁或抽共享纯函数，附金标单测）。信封 `data` SHALL 仅含白名单定位字段（至少 `case_id`）。SHALL NOT 仅依赖 `qc_alerts` 派生。SHALL NOT 对所有案件使用同一模板正文千人一面。SHALL NOT 把通话 session_id 写入投诉创建请求的 session_id。SHALL NOT 修改 `complaint_service.create` 的幂等语义：同一 case_id 不产生第二张工单。闭环接受 `complaint_id == case_id`。

#### Scenario: POST 体最小化

- **WHEN** 投诉建单（HTTP 或事件订阅 handler 内部 create）
- **THEN** 请求体只含 complaint_text 和 case_id，不含通话 session_id

#### Scenario: 重复推送不产生第二张工单

- **WHEN** 同一个 case_id 连续两次建单请求（HTTP 或重复 `complaint.created`）
- **THEN** 不产生第二张投诉工单

#### Scenario: complaint_text 按违规项差异化

- **WHEN** 两起案件的 qc_violations 命中项不同（一起坐席辱骂 speaker=agent、一起客户否认本人 speaker=user）
- **THEN** 两起投诉的 complaint_text 内容不同，分别反映各自命中的违规项

#### Scenario: 干净合规案件正文区分

- **WHEN** 某案件 qc_violations 无违规记录且 grade 为合规档
- **THEN** complaint_text 反映该案无命中违规项，SHALL NOT 复用坐席违规模板

#### Scenario: 事件 data 仅定位字段

- **WHEN** data-platform 发布 `complaint.created`
- **THEN** 信封 `data` 不含 complaint_text 正文；complaint-service 按 case_id 反查并组装正文后建单

### Requirement: 快照两路回流更新

`dw.ads_case_loop` 的更新来源只有两个，重复投递只覆盖、SHALL NOT 插第二行：
- data-platform 订阅 `qc.check.completed` 后写入质检列（`qc_record_id`、`compliance_score`、`risk_level`、`has_high_alert`）；详情经 HTTP 反拉；`has_high_alert` 按该 session_id 在 qc_alerts 中是否有 level=high 写成 `1` 或 `0`
- 投诉回写工具 `write_session` / `write_human_review_truth` 通过内部回写口 `PUT /api/v1/data/snapshot/{case_id}` 写入投诉列（`complaint_id`、`verdict`、`task_status`）。失败 SHALL 继续用投诉已有重试（3 次）和 `csmp:backflow:retry`，工单状态 SHALL NOT 因回写失败变成 500

data-platform SHALL 另订阅 `complaint.result.ready`（消费组 `gid_dp_complaint_result_ready`）用于确认/告警/可选 HTTP 反拉校验；SHALL NOT 用该事件替代 write_session 写投诉列。

#### Scenario: 质检完成写质检列

- **WHEN** data-platform 收到 qc.check.completed
- **THEN** 快照行的质检列被更新

#### Scenario: 投诉回写写投诉列

- **WHEN** 投诉回写工具被调用
- **THEN** 快照行的投诉列被更新，不产生新行

#### Scenario: 回写失败不阻塞工单

- **WHEN** 回写 3 次均失败
- **THEN** 工单状态正常推进，数据入 csmp:backflow:retry，快照行投诉列保持空

#### Scenario: 收到 result.ready 可确认

- **WHEN** data-platform 收到 `complaint.result.ready`
- **THEN** 记录确认日志或校验快照投诉列非空；SHALL NOT 因缺少该事件而由 DP HTTP 代审

### Requirement: 闭环验收脚本

data-platform SHALL 提供一条验收脚本（放 `data-platform/scripts/run_min_loop.py`，可重复执行），在 `EVENT_BUS=rocketmq` 下按以下顺序执行并校验：
1. 种子入库 + 快照空行
2. 发布 etl.task.done，质检真开检
3. 等待 qc.check.completed 回流质检列
4. 发布 `complaint.created`（投诉腿主路径）
5. 投诉侧 `SIM_AUTO_REVIEW=true`（验收环境）完成 judge→auto-approve→sample_backflow→write_session，并发布 `complaint.result.ready`
6. 确认快照质检列与投诉列均非空

验收 SHALL 确认：
- 验收前保证知识库里至少有一份仓库已有的法规/禁语短文能被 search_regulations 命中（用 qc-service 已有禁语字典或 rag-service 已有样例）
- 同一 case_id 在种子、qc_records、投诉工单、dw.ads_case_loop 四处对得上
- 质检分数来自真开检，不是人手插入 qc_records
- 质检完成之后才发布 `complaint.created` 并研判；人审 approve（含 SIM_AUTO_REVIEW）之后才出现投诉回流
- USE_MOCK_MCP=false 时法规来自 rag-service 真检索；失败走已有降级，进程不崩
- 快照行能看到质检分数和回写的研判结论
- USE_MOCK_MCP=true 的现有投诉测试、现有质检主路径、现有 RAG 主路径保持通过

#### Scenario: 闭环脚本可重复执行

- **WHEN** 连续两次执行 run_min_loop.py（EVENT_BUS=rocketmq）
- **THEN** 两次均通过验收，不产生重复数据

#### Scenario: 四处对得上

- **WHEN** 闭环脚本执行完毕
- **THEN** 种子的 case_id == qc_records 关联的 case_id == 投诉工单的 case_id == 快照行的 case_id

#### Scenario: 降级路径真走过

- **WHEN** 验收时模拟 rag-service 检索失败
- **THEN** 投诉侧走已有降级路径，进程不崩，验收记录可区分降级

#### Scenario: 两路回流都成功才算通过

- **WHEN** 闭环脚本执行完毕
- **THEN** 快照行的质检列和投诉列都有值；只有一侧回流成功时记告警，验收不通过

## ADDED Requirements

### Requirement: 发布 complaint.created 触发投诉闭环

data-platform 或闭环/批量脚本 SHALL 经统一 EventBus 发布 `complaint.created`，信封 `data` 仅白名单定位字段（至少 `case_id`；不预置 `complaint_no`）。批量场景通过控制发布条数限制研判负载，SHALL NOT 使用 `JUDGE_BUDGET`。`run_batch_flow.py` SHALL 在质检列回流后批量发布 `complaint.created`，SHALL NOT 等待 qc.check.completed 订阅器自动建投诉。

#### Scenario: 脚本发布 complaint.created

- **WHEN** run_min_loop 在质检列已更新后继续投诉腿
- **THEN** 发布一条 `complaint.created` 且 complaint-service 消费后产生工单并启动 judge

#### Scenario: 批量以事件条数控流

- **WHEN** run_batch_flow 需要约 N 单进入研判
- **THEN** 发布约 N 条 `complaint.created`，不依赖 data-platform JUDGE_BUDGET

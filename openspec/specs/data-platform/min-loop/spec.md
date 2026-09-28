# data-platform/min-loop

## Purpose

数据中台最小闭环：用同一套假数据把 RAG 检索、合规质检、智能投诉研判串成真链路——真推送、真消费、真检索、真回流，外加一张极简案件快照表。中台是薄底座，不建数仓、不建第二套工单体系。

## Requirements

### Requirement: 种子灌入已有三张业务表并写快照空行

data-platform SHALL 提供一个种子脚本（放 `data-platform/scripts/seed_min_loop.py`），使用固定 UUID `11111111-2222-4333-8444-555555555501` 作为 `cases.id`（也是 `call_records.case_id`、事件 `case_id`、投诉 `case_id`、快照 `case_id`）。SHALL NOT 使用文字号 `MIN_LOOP_CASE_001`。固定假值：客户名「张*」、`case_number=MIN-LOOP-001`、欠款与逾期天数固定、`account_stage=M1`。`phone_encrypted` 与 `phone_hash` 填假占位（不是真实手机号），`phone_hash` 为 64 位十六进制。禁止明文手机号、身份证、银行卡。
- `public.cases`：写入上述必填列。表上没有 `case_id` 列，主键是 `id`
- `public.call_records`：`session_id` 固定为 `min-loop-sess-001`，`case_id` 为上述 UUID，`call_type=ai`，`call_status=completed`，`end_time` 为 Asia/Shanghai 当天，`duration_seconds` > 0
- `public.dialog_turns`：`speaker` 只能是 `ai` 或 `user`，SHALL NOT 写 `agent`。对话主要中文，且含一句坐席（`speaker=ai`）高危话，能命中 qc-service 现有规则 R11、R12 或 R13
- 脚本 SHALL 先 upsert 三张业务表（按主键或 `session_id` 覆盖，不产生第二套主键），再 upsert `dw.ads_case_loop` 同一行的空行（填 case_id、客户名、欠款、逾期天数、通话 session_id、`call_ended='1'`；质检列和投诉列先空）
- 脚本失败 SHALL 整体重跑，不留半截数据
- 质检服务 SHALL NOT 自己 INSERT `call_records`（保持现有红线）

#### Scenario: 重复执行不产生第二套主键

- **WHEN** 连续两次执行种子脚本
- **THEN** 三张业务表和快照表的 case_id 均只有一行，不产生重复主键

#### Scenario: 快照空行字段对齐

- **WHEN** 种子脚本执行完毕
- **THEN** `dw.ads_case_loop` 存在一行，含该 UUID、客户名、欠款、逾期天数、通话 session_id、`call_ended='1'`，质检列和投诉列为空

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

### Requirement: dw.ads_case_loop 快照表

data-platform SHALL 维护 `dw.ads_case_loop` 表，一行一个 case_id。列 SHALL 恰好为以下 17 列（英文列名，SHALL NOT 用中文列名）：

| 英文列名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `case_id` | text/varchar | 唯一索引，一行一案件 |
| `customer_name` | text | 客户名，脱敏值（如「张*」） |
| `overdue_amount` | numeric | 欠款，对齐 public.cases |
| `overdue_days` | int | 逾期天数，对齐 public.cases |
| `session_id` | text | 通话 session_id，供桥反查 |
| `call_ended` | char(1) | 通话是否结束，只能是 `0` 或 `1`，禁止 boolean |
| `qc_record_id` | text | 沿用 qc_records 对外主键名（= qc_records.id） |
| `compliance_score` | 沿用 qc_records | 质检分数，沿用 qc_records 库列名 |
| `risk_level` | 沿用 qc_records | 风险，沿用 qc_records 库列名 |
| `has_high_alert` | char(1) | 是否有高危告警，只能是 `0` 或 `1`，禁止 boolean |
| `complaint_id` | text/uuid | 本闭环等于 case_id |
| `verdict` | text | 研判结论（write_session 入参 verdict） |
| `task_status` | text | 任务状态 |
| `backflow_at` | timestamptz | 最后回流时间，Asia/Shanghai 写入 |
| `product_type` | text | 案件产品类型，对齐 public.cases，供 business_ticket 桥读取 |
| `repayment_records` | jsonb | 还款记录，对齐 public.cases，供 business_ticket 桥读取 |
| `create_time` | timestamptz | 案件创建时间，对齐 public.cases，供 business_ticket 桥读取 |

`compliance_score`、`risk_level` 两列 SHALL 沿用 `qc_records` 现有库列名（SHALL NOT 命名为 `score`/`risk`），SHALL NOT 新造字段。`has_high_alert` SHALL 由 data-platform 在收到 `qc.check.completed` 后查询同一 `session_id` 的 `qc_alerts`：存在 `level=high` 且未删除则为 `1`，否则为 `0`。事件 payload 没有该字段，SHALL NOT 向 qc-service 新增接口。最后回流时间 SHALL 使用 Asia/Shanghai 时区。`product_type`、`repayment_records`、`create_time` 三列 SHALL 在 data-platform 预处理入库时同步写入快照表对应行，SHALL NOT 留空由桥固定填空串/空列表。SHALL NOT 新增用户特征、日报、策略效果、第二张宽表。

#### Scenario: 一行一案件

- **WHEN** 重复 upsert 同一个 case_id
- **THEN** 只覆盖更新那一行，不插第二行

#### Scenario: 列数固定且为英文列名

- **WHEN** 检查 dw.ads_case_loop 表结构
- **THEN** 列恰好为上述 17 列英文列名，不多不少，无中文列名

#### Scenario: 新三列预处理时写入

- **WHEN** data-platform 预处理入库某案件到快照表
- **THEN** `product_type`、`repayment_records`、`create_time` 三列均写入对应业务值，非空串/空列表

### Requirement: 快照只读接口

data-platform SHALL 提供只读接口 `GET /api/v1/data/snapshot/{case_id}`，返回 `dw.ads_case_loop` 的那一行，供投诉进程内桥反查 session_id 使用。响应 SHALL 沿用统一格式 `{code, message, data, trace_id}`，SHALL NOT 新增错误码段。SHALL NOT 提供报表 API、用户特征、日报、第二张宽表。

#### Scenario: 按 case_id 读快照

- **WHEN** GET /api/v1/data/snapshot/11111111-2222-4333-8444-555555555501
- **THEN** 返回 code=0，data 含该行全部 17 列

#### Scenario: 沿用统一响应格式

- **WHEN** 调用只读接口
- **THEN** 响应体为 {code, message, data, trace_id}，不新增错误码段

### Requirement: 快照内部回写口

complaint-service 回写桥 SHALL 通过 `PUT /api/v1/data/snapshot/{case_id}` 更新快照投诉列。请求体只含 `complaint_id`、`verdict`、`task_status`。data-platform SHALL 只更新该 case_id 的那一行。响应沿用 `{code, message, data, trace_id}`，SHALL NOT 新增错误码段，SHALL NOT 新做登录。该 PUT SHALL NOT 加入 api-gateway 的对外路由；本机闭环可以调用。

#### Scenario: 回写只更新该 case_id 行

- **WHEN** PUT /api/v1/data/snapshot/{case_id} 携带投诉列字段（complaint_id、verdict、task_status）
- **THEN** 只更新该 case_id 的这三列，不新增行，不改其它案件

#### Scenario: 回写口不进对外网关

- **WHEN** 检查 api-gateway 路由
- **THEN** 没有把 PUT /api/v1/data/snapshot/{case_id} 配成对外入口，且没有新增登录

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

### Requirement: Redis 库号约束

`etl.task.done` 和 `qc.check.completed` SHALL 走 qc-service 已在用的 Redis，库号为 `QC_REDIS_DB`（默认 3）。SHALL NOT 发到 0 号库。

#### Scenario: 事件不进 0 号库

- **WHEN** data-platform 发布 etl.task.done 或订阅 qc.check.completed
- **THEN** Redis 库号为 QC_REDIS_DB（默认 3），不是 0

### Requirement: 安全约束

data-platform SHALL NOT 新增对公网开放的任意写入接口。种子脚本 SHALL 用运维脚本或仅限本机的已有模式。SHALL NOT 新做登录体系。回写 SHALL 只更新该 case_id 的那一行，SHALL NOT 用请求体改写别的案件。种子与快照中的电话、证件、卡号 SHALL 是假数据或脱敏值。

#### Scenario: 不新增公网写入接口

- **WHEN** 检查 data-platform 的接口
- **THEN** 快照写入只有 PUT /api/v1/data/snapshot/{case_id}，且未加入 api-gateway 对外路由，没有新登录

#### Scenario: 回写不串案件

- **WHEN** 回写请求携带 case_id
- **THEN** 只更新该 case_id 的那一行，不影响其它案件行

### Requirement: 批量案件生成脚本

data-platform SHALL 提供批量案件生成脚本 `services/data-platform/scripts/gen_cases_batch.py`，支持 `--count`（默认 200）与 `--seed`（默认 42）参数。每条案件 SHALL 包含以下 20 字段契约：`case_id`（UUID）、`case_number`（唯一，格式 `BATCH-YYYYMMDD-NNNN`）、`case_source`（`APP`/`OUTSRC`/`WECHAT` 之一）、`customer_name`、`phone`（匹配 `1[3-9]\d{9}`）、`id_card`（18 位含 ISO 7064 MOD-11 校验位；落库列为 `id_card_hash`，非 18 位原值）、`gender`（由身份证第 17 位奇偶派生 `M`/`F`，未派生时 `U`）、`product_type`（`信用贷`/`消费分期`/`信用卡代偿` 之一）、`principal`（1 万~50 万）、`overdue_amount`（≤ principal×1.5）、`overdue_days`（1~360）、`interest_rate`（日利率）、`loan_date`、`due_date`（晚于 loan_date）、`overdue_date`（晚于 due_date）、`repayment_records`（0~5 条 JSONB）、`account_stage`（由 overdue_days 派生：≤30→`M0`/31~60→`M1`/61~90→`M2`/>90→`M3`）、`case_status`、`stop_call_status`（默认 `callable`）、`create_time`。

PII 处理：`phone` SHALL 经 `crypto.py` 的 `encrypt()`（AES-256-GCM）加密并计算 SHA-256 哈希后落库，明文即弃；`id_card` SHALL 仅计算 SHA-256 哈希后落库，明文即弃；SHALL NOT 在任何持久化存储或日志中保留明文手机号、身份证、银行卡号。脚本 SHALL 单事务幂等 upsert（按 `case_id`/`case_number` 覆盖，不产生第二套主键），重跑不重复。脚本 SHALL 在写入后执行自检：手机号正则全数通过、身份证 ISO 7064 MOD-11 校验位重算全数通过、零明文 PII（grep 不到明文样例）、`--count` 与落库行数一致。自检失败 SHALL 退出非零并回滚或留下明确告警。

#### Scenario: 重跑不重复

- **WHEN** 连续两次执行 `gen_cases_batch.py --count 200 --seed 42`
- **THEN** cases 表中按 seed 42 生成的 case_id 均只有一行，不产生重复主键

#### Scenario: PII 不留明文

- **WHEN** 检查脚本写入后的 cases 表与日志
- **THEN** 手机号列为加密值+哈希值，身份证列为哈希值；按列值整值锚定判定，无任何列的完整值匹配 `1[3-9]\d{9}` 手机号正则或 18 位身份证正则（SHA-256 哈希内部偶然出现的数字子串不算明文）

#### Scenario: 校验位重算通过

- **WHEN** 对生成的全部 id_card 重算 ISO 7064 MOD-11 校验位
- **THEN** 全部通过，自检输出 count 一致

#### Scenario: 字段契约齐全

- **WHEN** 检查任一生成行
- **THEN** 20 字段全部存在且类型符合契约（含 `case_number` 唯一、`account_stage` 由 `overdue_days` 派生、`gender` 由身份证派生）

### Requirement: 批量通话生成脚本

data-platform SHALL 提供批量通话生成脚本 `services/data-platform/scripts/gen_calls_batch.py`，支持 `--count`（默认 200）与 `--seed`（默认 42）参数。主表字段 SHALL 与现有 `call_records` 表（0001 迁移定义）完全对齐，`agent_id` 可空。明细 SHALL 与 `dialog_turns` 对齐，`speaker` CHECK 约束限定 `('ai','user')`，SHALL NOT 写 `agent`。

200 通通话 SHALL 各不相同：由场景骨架×话术变体池×随机槽位×可选分支组合生成，生成后 SHALL 经哈希去重，SHALL NOT 出现两通完全相同的对话内容。分布 SHALL 约为 60% 干净合规 / 25% 客户侧异常（否认本人、要求停催、声称已还）/ 15% 坐席违规（冒充法院、威胁、辱骂、骚扰第三人）。`call_status` SHALL 约 85% 为 `completed`，其余为 `no_answer`/`failed`/短通话。轮次 SHALL 在 2~12 之间不等。通话时间 SHALL 落在对应案件 `create_time` 之后的随机时点。脚本 SHALL 单事务幂等 upsert（按 `session_id`/`case_id` 覆盖），重跑不重复。

#### Scenario: 重跑不重复

- **WHEN** 连续两次执行 `gen_calls_batch.py --count 200 --seed 42`
- **THEN** call_records/dialog_turns 表中按 seed 42 生成的 session_id 均只有一组，不产生重复主键

#### Scenario: speaker 仅 ai 或 user

- **WHEN** 检查生成的 dialog_turns.speaker 全部分布
- **THEN** 只出现 `ai` 或 `user`，不出现 `agent` 或其它值

#### Scenario: 分布命中

- **WHEN** 统计 200 通通话的违规分布
- **THEN** 干净合规约占 60%、客户侧异常约占 25%、坐席违规约占 15%，偏差在 ±5 个百分点内

#### Scenario: 200 通各不相同

- **WHEN** 对全部 200 通对话内容计算哈希
- **THEN** 哈希值集合大小等于通话数，无重复

#### Scenario: 通话时间晚于案件创建

- **WHEN** 检查任一通话的通话时间与对应案件 create_time
- **THEN** 通话时间晚于该案件 create_time

### Requirement: 批量快照行预处理

data-platform SHALL 在 `gen_cases_batch.py` 与 `gen_calls_batch.py` 均执行完成后，对每个生成的 case_id upsert 一行 `dw.ads_case_loop` 快照行，组装 cases 侧与 calls 侧字段：从 `public.cases` 同行读 `customer_name`、`overdue_amount`、`overdue_days`、`product_type`、`repayment_records`、`create_time`；从 `public.call_records` 同 case_id 行读 `session_id`、`call_ended`（通话 completed 则 `1`，否则 `0`）。`call_ended` SHALL 为 char(1) `0`/`1`，禁止 boolean。该预处理 SHALL 写入上述字段及 3 个新列（`product_type`、`repayment_records`、`create_time`），质检列（`qc_record_id`/`compliance_score`/`risk_level`/`has_high_alert`）与投诉列（`complaint_id`/`verdict`/`task_status`/`backflow_at`）SHALL 留空，由后续 `qc.check.completed` 订阅与 `write_session` 回流分别填充。预处理 SHALL 单事务幂等 upsert（按 `case_id` 覆盖），重跑不重复、SHALL NOT 产生第二行。SHALL NOT 在此步写入质检或投诉列。

#### Scenario: 200 行快照行就绪

- **WHEN** gen_cases_batch + gen_calls_batch + 快照预处理执行完毕
- **THEN** dw.ads_case_loop 有 200 行，每行含 cases 侧 + calls 侧字段 + 3 新列，质检列与投诉列为空

#### Scenario: 重跑不重复

- **WHEN** 连续两次执行快照预处理
- **THEN** 每个 case_id 仍只有一行，只覆盖更新，不产生第二行

#### Scenario: call_ended 为 char(1)

- **WHEN** 检查快照行 call_ended
- **THEN** 值为 `0` 或 `1`，非 boolean

### Requirement: 批量全链路对账验收

data-platform SHALL 提供批量对账验收（可复用 `run_min_loop.py` 扩展或新增脚本），在 200 案件 + 200 通话批量流量下校验全链路。验收 SHALL 确认：

- 推送数（发布的 `etl.task.done`/`qc.check.completed` 相关计数）= 消费数（订阅器实际处理的 case_id 数）= 落库数（complaints 表与 dw.ads_case_loop 快照表的 case_id 数），三者一致
- 零重复工单：同一 case_id 在 complaints 表只有一行、在快照表只有一行
- 快照质检列（`qc_record_id`、`compliance_score`、`risk_level`、`has_high_alert`）与投诉列（`complaint_id`、`verdict`、`task_status`）两侧回流齐全：被处理子集的两列均非空
- 限流命中（rate_limit.hit 至少触发一次）、熔断 open→half-open→closed 恢复（触发并恢复）、队列排空（最终 `csmp:backflow:retry` 与在途队列归零或只剩已知失败重试）

judge 跑图 SHALL 放有界子集（受 LLM QPS 与成本约束，建议约 20 单），投诉创建可全量洪峰。验收 SHALL 包含中途 kill 消费者进程制造丢任务场景：kill 后重启消费者，验证丢任务被补投或入重试队列，为 P5 Stream 升级提供证据。kill 场景 SHALL 可控、可恢复，SHALL NOT 留下不可恢复的脏数据。

#### Scenario: 推送=消费=落库

- **WHEN** 批量流量跑完后统计计数
- **THEN** 推送数 == 消费数 == 落库数，三者一致

#### Scenario: 零重复工单

- **WHEN** 对 complaints 表与 dw.ads_case_loop 按 case_id 分组
- **THEN** 每个 case_id 均只出现一行，无重复

#### Scenario: 两路回流齐全

- **WHEN** 检查被处理子集的快照行
- **THEN** 质检列与投诉列均非空；只有一侧回流成功时记告警，验收不通过

#### Scenario: 限流命中

- **WHEN** 检查 `csmp.rate_limit.hit` 日志
- **THEN** 至少命中一次，证明限流零件在真压力下生效

#### Scenario: 熔断恢复

- **WHEN** 触发熔断 open 后等待 reset_timeout
- **THEN** 熔断经 half-open 回到 closed，后续请求正常通过

#### Scenario: 队列排空

- **WHEN** 批量流量跑完且等待排空
- **THEN** `csmp:backflow:retry` 与在途队列归零或只剩已知失败重试项

#### Scenario: kill 消费者后丢任务可恢复

- **WHEN** 中途 kill 消费者进程制造丢任务，随后重启消费者
- **THEN** 丢任务被补投或入重试队列，最终对账仍能平衡，无不可恢复脏数据

#### Scenario: judge 有界子集

- **WHEN** 批量流量中 judge 跑图
- **THEN** judge 只对约 20 单跑图（受 LLM 瓶颈与成本约束），投诉创建可全量洪峰

### Requirement: 发布 call.session.ended 逐条质检

一键生成 30 条通话后，data-platform SHALL 逐条通过统一事件总线发布 `call.session.ended`，每条 payload 仅含 `session_id`。qc-service 订阅后反拉 qc-source 执行质检。data-platform SHALL NOT 批量合并发布。

#### Scenario: 30 条通话逐条发事件

- **WHEN** 一键生成 30 条通话完成
- **THEN** 发布 30 条独立的 `call.session.ended` 事件，每条 payload 仅含 `session_id`

### Requirement: 发布 complaint.created 触发投诉闭环

data-platform 或闭环/批量脚本 SHALL 经统一 EventBus 发布 `complaint.created`，信封 `data` 仅白名单定位字段（至少 `case_id`；不预置 `complaint_no`）。批量场景通过控制发布条数限制研判负载，SHALL NOT 使用 `JUDGE_BUDGET`。`run_batch_flow.py` SHALL 在质检列回流后批量发布 `complaint.created`，SHALL NOT 等待 qc.check.completed 订阅器自动建投诉。

#### Scenario: 脚本发布 complaint.created

- **WHEN** run_min_loop 在质检列已更新后继续投诉腿
- **THEN** 发布一条 `complaint.created` 且 complaint-service 消费后产生工单并启动 judge

#### Scenario: 批量以事件条数控流

- **WHEN** run_batch_flow 需要约 N 单进入研判
- **THEN** 发布约 N 条 `complaint.created`，不依赖 data-platform JUDGE_BUDGET

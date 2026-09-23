## Purpose

数据中台最小闭环：用同一套假数据把 RAG 检索、合规质检、智能投诉研判串成真链路——真推送、真消费、真检索、真回流，外加一张极简案件快照表。中台是薄底座，不建数仓、不建第二套工单体系。

## ADDED Requirements

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

种子入库后，data-platform SHALL 发布已有事件 `etl.task.done` 到 qc-service 已在用的 Redis（库号 `QC_REDIS_DB`，默认 3），payload 含 `backfill_date` 等于种子通话结束那天。data-platform SHALL NOT 用 HTTP POST 调用 qc-service。qc-service 已有订阅 `etl.task.done` 的机制，且 `QC_AUTO_COMPLEMENT_AFTER_ETL` 保持默认 true，收到后 GET pending-qc + qc-source 走离线质检。data-platform SHALL NOT 修改 `QC_AUTO_COMPLEMENT_AFTER_ETL` 的默认值。发布后 SHALL 记日志（含 case_id、session_id、backfill_date、发布时间），失败 SHALL 留下可查记录，SHALL NOT 假装成功。

#### Scenario: etl.task.done 发到正确库号

- **WHEN** 种子入库并写快照空行后
- **THEN** `etl.task.done` 发布到 `QC_REDIS_DB`（默认 3），不发布到 0 号库

#### Scenario: data-platform 不直接调 qc

- **WHEN** data-platform 触发质检
- **THEN** 不发 HTTP POST 到 qc-service，仅通过 Redis 事件

#### Scenario: 发布后留下可查记录

- **WHEN** data-platform 发布 etl.task.done
- **THEN** 日志含 case_id、session_id、backfill_date、发布时间；发布失败也留下可查记录

### Requirement: 订阅 qc.check.completed 触发推送投诉

data-platform SHALL 订阅已有事件 `qc.check.completed`（走 `QC_REDIS_DB`），收到该事件且确认 `qc_records` 已落库后，HTTP 调用已有 `POST /api/v1/complaints` 创建投诉工单。data-platform SHALL NOT 轮询、SHALL NOT sleep 等待、SHALL NOT 在未收到事件前就推投诉。`qc.check.completed` payload 已含 `{qc_record_id, session_id, call_id, case_id, score, risk, grade, trace_id, ts}`。若 N 分钟（建议 10 分钟）内未收到匹配 case_id 的 `qc.check.completed`，data-platform SHALL 记告警日志（含 case_id、等待时长），SHALL NOT 无限阻塞。

#### Scenario: 收到事件后才推投诉

- **WHEN** data-platform 收到 `qc.check.completed` 且 case_id 匹配种子案件
- **THEN** HTTP POST /api/v1/complaints 创建投诉工单

#### Scenario: 未收到事件不推

- **WHEN** data-platform 未收到 `qc.check.completed`
- **THEN** 不调用 POST /api/v1/complaints

#### Scenario: 超时未收到事件记告警

- **WHEN** 种子入库后 N 分钟内未收到匹配 case_id 的 qc.check.completed
- **THEN** 记告警日志（含 case_id、等待时长），不无限阻塞

### Requirement: 禁止用 qc.complaint.suspected 自动建投诉工单

data-platform SHALL NOT 用 `qc.complaint.suspected` 事件自动新建投诉工单。该事件保持现有语义：只给已经在途的投诉补证。投诉创建的唯一触发源是 `qc.check.completed` 事件。

#### Scenario: qc.complaint.suspected 不触发建单

- **WHEN** data-platform 收到 `qc.complaint.suspected` 事件
- **THEN** 不调用 POST /api/v1/complaints，该事件仅用于已在途投诉的补证

### Requirement: 投诉创建后调用 judge 触发研判

投诉创建 SHALL 分三步，SHALL NOT 改图节点，SHALL NOT 新做路由：
1. `POST /api/v1/complaints` 创建工单（拿到 `complaint_id`）
2. `POST /api/v1/complaints/{complaint_id}/judge`，body `{"run_graph": true}`。图会停在 `human_review`，此时 SHALL NOT 已经调用 `write_session`
3. `PUT /api/v1/complaints/{complaint_id}/review`，body 使用已有 `ReviewRequest`，`review_action=approve`。该调用 SHALL 对同一研判线程执行 `Command(resume="approve")`，使图进入已有 `sample_backflow`

`judge` 使用的 checkpointer SHALL 按 `complaint_id` 留在投诉进程内，直到这次 review。SHALL NOT 每次 judge 新建一个请求结束就丢弃的 MemorySaver。现有 `PUT /review` 路径保留，不新增接口。`run_graph` 只属于 judge 的 `JudgeRequest`。

#### Scenario: 创建后调用 judge 停在人审

- **WHEN** data-platform POST judge 且 run_graph=true
- **THEN** 研判停在 human_review，快照投诉列仍为空

#### Scenario: 人审通过后才回流

- **WHEN** 随后 PUT /review 且 review_action=approve
- **THEN** 同一线程恢复，进入 sample_backflow，并调用 write_session

#### Scenario: 不在创建接口传 run_graph

- **WHEN** data-platform 调用 POST /api/v1/complaints
- **THEN** 请求体只含 complaint_text 和 case_id，不含 run_graph（run_graph 不属于创建接口字段）

### Requirement: 投诉创建只传 complaint_text 和 case_id

data-platform 推送投诉时，POST 体 SHALL 只带 `complaint_text` 和 `case_id`（case_id 必须是种子那条案件）。SHALL NOT 把通话 session_id 写入投诉创建请求的 session_id（该字段在现有代码里是研判会话号）。SHALL NOT 修改 `complaint_service.create`。现有实现在传入 case_id 时沿用它作为工单号，闭环接受 `complaint_id == case_id`。重复 POST 同一个 case_id SHALL NOT 产生第二张工单，靠现有行为，SHALL NOT 另做幂等表。

#### Scenario: POST 体最小化

- **WHEN** data-platform 推送投诉
- **THEN** 请求体只含 complaint_text 和 case_id，不含通话 session_id

#### Scenario: 重复推送不产生第二张工单

- **WHEN** 同一个 case_id 连续两次 POST /api/v1/complaints
- **THEN** 不产生第二张投诉工单

### Requirement: dw.ads_case_loop 快照表

data-platform SHALL 新增一张表 `dw.ads_case_loop`，一行一个 case_id。列 SHALL 恰好为以下 14 列（英文列名，SHALL NOT 用中文列名）：

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

`compliance_score`、`risk_level` 两列 SHALL 沿用 `qc_records` 现有库列名（SHALL NOT 命名为 `score`/`risk`），SHALL NOT 新造字段。`has_high_alert` SHALL 由 data-platform 在收到 `qc.check.completed` 后查询同一 `session_id` 的 `qc_alerts`：存在 `level=high` 且未删除则为 `1`，否则为 `0`。事件 payload 没有该字段，SHALL NOT 向 qc-service 新增接口。最后回流时间 SHALL 使用 Asia/Shanghai 时区。SHALL NOT 新增用户特征、日报、策略效果、第二张宽表。

#### Scenario: 一行一案件

- **WHEN** 重复 upsert 同一个 case_id
- **THEN** 只覆盖更新那一行，不插第二行

#### Scenario: 列数固定且为英文列名

- **WHEN** 检查 dw.ads_case_loop 表结构
- **THEN** 列恰好为上述 14 列英文列名，不多不少，无中文列名

### Requirement: 快照只读接口

data-platform SHALL 提供只读接口 `GET /api/v1/data/snapshot/{case_id}`，返回 `dw.ads_case_loop` 的那一行，供投诉进程内桥反查 session_id 使用。响应 SHALL 沿用统一格式 `{code, message, data, trace_id}`，SHALL NOT 新增错误码段。SHALL NOT 提供报表 API、用户特征、日报、第二张宽表。

#### Scenario: 按 case_id 读快照

- **WHEN** GET /api/v1/data/snapshot/11111111-2222-4333-8444-555555555501
- **THEN** 返回 code=0，data 含该行全部 14 列

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
- data-platform 订阅 `qc.check.completed` 后写入质检列（`qc_record_id`、`compliance_score`、`risk_level`、`has_high_alert`）。事件 JSON 键映射：`score`→列 `compliance_score`、`risk`→列 `risk_level`、`grade` 不落库；`has_high_alert` 按该 session_id 在 qc_alerts 中是否有 level=high 写成 `1` 或 `0`
- 投诉回写工具 `write_session` / `write_human_review_truth` 通过内部回写口 `PUT /api/v1/data/snapshot/{case_id}` 写入投诉列（`complaint_id`、`verdict`、`task_status`）。失败 SHALL 继续用投诉已有重试（3 次）和 `csmp:backflow:retry`，工单状态 SHALL NOT 因回写失败变成 500

#### Scenario: 质检完成写质检列

- **WHEN** data-platform 收到 qc.check.completed
- **THEN** 快照行的质检列被更新

#### Scenario: 投诉回写写投诉列

- **WHEN** 投诉回写工具被调用
- **THEN** 快照行的投诉列被更新，不产生新行

#### Scenario: 回写失败不阻塞工单

- **WHEN** 回写 3 次均失败
- **THEN** 工单状态正常推进，数据入 csmp:backflow:retry，快照行投诉列保持空

### Requirement: 闭环验收脚本

data-platform SHALL 提供一条验收脚本（放 `data-platform/scripts/run_min_loop.py`，可重复执行），按以下顺序执行并校验：
1. 种子入库 + 快照空行
2. 发布 etl.task.done，质检真开检
3. 订阅 qc.check.completed 后推投诉
4. 投诉研判走真 RAG（USE_MOCK_MCP=false），judge 停在人审
5. PUT /review 且 review_action=approve，图进入 sample_backflow，两路回流写入同一行快照

验收 SHALL 确认：
- 验收前保证知识库里至少有一份仓库已有的法规/禁语短文能被 search_regulations 命中（用 qc-service 已有禁语字典或 rag-service 已有样例）
- 同一 case_id 在种子、qc_records、投诉工单、dw.ads_case_loop 四处对得上
- 质检分数来自真开检，不是人手插入 qc_records
- 质检完成之后才创建投诉并研判；人审 approve 之后才出现投诉回流
- USE_MOCK_MCP=false 时法规来自 rag-service 真检索；失败走已有降级，进程不崩
- 快照行能看到质检分数和回写的研判结论
- USE_MOCK_MCP=true 的现有投诉测试、现有质检主路径、现有 RAG 主路径保持通过

#### Scenario: 闭环脚本可重复执行

- **WHEN** 连续两次执行 run_min_loop.py
- **THEN** 两次均通过验收，不产生重复数据

#### Scenario: 四处对得上

- **WHEN** 闭环脚本执行完毕
- **THEN** 种子的 case_id == qc_records 关联的 case_id == 投诉工单的 case_id == 快照行的 case_id

#### Scenario: 降级路径真走过

- **WHEN** 验收时模拟 rag-service 检索失败
- **THEN** 研判日志含降级标记（如 `[规则兜底]` 前缀或降级事件），进程不崩

#### Scenario: 两路回流都成功才算通过

- **WHEN** 闭环验收结束
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

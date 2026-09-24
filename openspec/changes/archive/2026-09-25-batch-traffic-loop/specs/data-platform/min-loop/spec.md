## MODIFIED Requirements

### Requirement: 订阅 qc.check.completed 触发推送投诉

data-platform SHALL 订阅已有事件 `qc.check.completed`（走 `QC_REDIS_DB`），收到该事件且确认 `qc_records` 已落库后，HTTP 调用已有 `POST /api/v1/complaints` 创建投诉工单。订阅器 SHALL 处理 payload 中的任意 `case_id`，SHALL NOT 硬编码单个 `SEED_CASE_ID`（如 `11111111-2222-4333-8444-555555555501`）只接受该固定值。同一 `case_id` 的在途/已处理判定 SHALL 保留幂等与在途守卫，重复投递只覆盖、SHALL NOT 产生第二张工单或重复跑图。data-platform SHALL NOT 轮询、SHALL NOT sleep 等待、SHALL NOT 在未收到事件前就推投诉。`qc.check.completed` payload 已含 `{qc_record_id, session_id, call_id, case_id, score, risk, grade, trace_id, ts}`。若 N 分钟（建议 10 分钟）内未收到匹配 case_id 的 `qc.check.completed`，data-platform SHALL 记告警日志（含 case_id、等待时长），SHALL NOT 无限阻塞。投诉创建 SHALL 对每个 case_id 全量执行；judge 与 review SHALL 受有界预算约束（`--judge-budget`，建议约 20 单），仅子集跑图，其余投诉停在"已创建未研判"状态，SHALL NOT 对全量 case_id 跑 judge。

#### Scenario: 收到事件后才推投诉

- **WHEN** data-platform 收到 `qc.check.completed` 且 case_id 匹配任一已入库案件
- **THEN** HTTP POST /api/v1/complaints 创建投诉工单

#### Scenario: 未收到事件不推

- **WHEN** data-platform 未收到 `qc.check.completed`
- **THEN** 不调用 POST /api/v1/complaints

#### Scenario: 超时未收到事件记告警

- **WHEN** 种子入库后 N 分钟内未收到匹配 case_id 的 qc.check.completed
- **THEN** 记告警日志（含 case_id、等待时长），不无限阻塞

#### Scenario: 任意 case_id 均被处理

- **WHEN** data-platform 收到 `qc.check.completed` 携带非 `SEED_CASE_ID` 的 case_id
- **THEN** 订阅器对该 case_id 执行投诉创建（必经）；judge 与 review 按 `--judge-budget` 有界子集执行，非预算内 case_id 的投诉停在"已创建未研判"，不因 case_id 非固定值而拒绝或跳过

#### Scenario: 在途守卫保留

- **WHEN** 同一 case_id 短时间内连续收到两次 `qc.check.completed`
- **THEN** 只创建一张投诉工单，不重复跑图，幂等守卫生效

### Requirement: 投诉创建只传 complaint_text 和 case_id

data-platform 推送投诉时，POST 体 SHALL 只带 `complaint_text` 和 `case_id`。`complaint_text` SHALL 按该案件实际命中的违规项组装差异化正文：违规项 SHALL 由该 session_id 在 `qc_violations` 中的违规记录（含 `speaker`/`redline_tag`）派生，并以 `qc.check.completed` payload 的 `risk`/`grade` 作一致性校验信号（如坐席违规（speaker=agent）→`agent_violation` 类正文，客户侧异常（speaker=user）→`user_misunderstanding` 类正文，干净合规→`compliant` 类正文）。SHALL NOT 仅依赖 `qc_alerts` 派生（该表仅含 agent high 违规，无法区分客户侧异常）。SHALL NOT 对所有案件使用同一模板正文千人一面。SHALL NOT 把通话 session_id 写入投诉创建请求的 session_id（该字段在现有代码里是研判会话号）。SHALL NOT 修改 `complaint_service.create`。现有实现在传入 case_id 时沿用它作为工单号，闭环接受 `complaint_id == case_id`。重复 POST 同一个 case_id SHALL NOT 产生第二张工单，靠现有行为，SHALL NOT 另做幂等表。

#### Scenario: POST 体最小化

- **WHEN** data-platform 推送投诉
- **THEN** 请求体只含 complaint_text 和 case_id，不含通话 session_id

#### Scenario: 重复推送不产生第二张工单

- **WHEN** 同一个 case_id 连续两次 POST /api/v1/complaints
- **THEN** 不产生第二张投诉工单

#### Scenario: complaint_text 按违规项差异化

- **WHEN** 两起案件的 qc_violations 命中项不同（一起坐席辱骂 speaker=agent、一起客户否认本人 speaker=user）
- **THEN** 两起投诉的 complaint_text 内容不同，分别反映各自命中的违规项

#### Scenario: 干净合规案件正文区分

- **WHEN** 某案件 qc_violations 无违规记录且 grade 为合规档
- **THEN** complaint_text 反映该案无命中违规项，SHALL NOT 复用坐席违规模板

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

## ADDED Requirements

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

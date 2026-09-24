## Context

complaint-service-graph-fix 已让单案件真路径闭环 13/13 PASS（进程内三桥 + MemorySaver + 图状态机 + 两路回流均就绪）。当前链路仅覆盖一单，且 data-platform 订阅器硬编码 `SEED_CASE_ID=11111111-...501`、`dw.ads_case_loop` 缺 `product_type`/`repayment_records`/`create_time` 三列导致 business_ticket 桥读快照时这三字段恒空、投诉正文千人一面。本 change 把链路从单案件扩展到 200 案件 + 200 通话批量流量，验证限流/熔断/幂等/队列/三级超时零件在真压力下是否可用，并补全上述 3 个已知缺口（G1/G2/G3）。详见 proposal.md。

约束：
- 禁止修改 specs 00~07、qc-service/rag-service 生产代码、现有测试用例
- 禁止手插 qc_records、造假回写、git commit（完成后等待人工审查）
- 不得新增对外接口；快照写入仅 `PUT /api/v1/data/snapshot/{case_id}`，不进 api-gateway 对外路由
- Redis 必须走 3 号库（`QC_REDIS_DB`），禁止 0 号库
- 数据库索引/迁移必须先在 staging 验证
- AI 无最终业务责任判定权，所有输出需人工审核

## Goals / Non-Goals

**Goals:**
- 通过迁移 0014 给 `cases` +10 列、`dw.ads_case_loop` +3 列，让 business_ticket 桥从快照读到 `product_type`/`repayment_records`/`create_time` 真值（G1）
- 提供可重复执行的 `gen_cases_batch.py` / `gen_calls_batch.py`，生成 200 条差异化案件 + 200 通差异化通话，PII 全程加密/哈希、零明文
- 泛化订阅器处理任意 case_id（G2），投诉正文按实际违规项差异化组装（G3）
- 在批量流量下完成对账验收（推送=消费=落库、零重复、两路回流齐全、限流命中、熔断恢复、队列排空、kill 消费者丢任务可恢复），judge 跑图放有界子集（约 20 单）

**Non-Goals:**
- 不改图节点、不新增路由、不新增对外接口
- 不改 specs 00~07、qc-service/rag-service 生产代码、现有测试用例
- 不实现历史工单/历史投诉桥的"真"实现（保持恒返 []，不新建表）
- 不引入坐席 API（users 表已有数据，agent_id 可空，0001 迁移已 `ON DELETE SET NULL`）
- 不做 P5 Stream 升级本身（本 change 仅拿 kill 消费者丢任务证据，供 P5 决策）
- 不改 `complaint_service.create`、不改 `sample_backflow` 节点

## Decisions

### D1：迁移 0014 采用 ADD COLUMN ... NULL/DEFAULT，downgrade DROP COLUMN

**选择**：`ALTER TABLE public.cases ADD COLUMN ... NULL` 或 `DEFAULT ...`；`dw.ads_case_loop` 同样 `ADD COLUMN ... NULL`。downgrade 用 `DROP COLUMN IF EXISTS`。
**理由**：全部可空/带默认，不碰现有行；downgrade 可干净回滚；与 0001 迁移（agent_id 可空、`ON DELETE SET NULL`）风格一致。
**备选**：重建表 → 否决，风险高且影响在途数据。
**staging 前置**：按项目硬约束，索引/迁移先在 staging 验证后再上生产。

### D2：cases 新增 10 列与 `dw.ads_case_loop` 新增 3 列的列名与类型

**选择**（cases +10）：
- `case_source` text CHECK in (`APP`,`OUTSRC`,`WECHAT`)
- `gender` char(1) CHECK in (`M`,`F`,`U`)
- `product_type` text
- `principal` numeric
- `interest_rate` numeric
- `loan_date` date
- `due_date` date
- `overdue_date` date
- `repayment_records` jsonb DEFAULT `'[]'`
- `stop_call_status` text DEFAULT `callable`

**选择**（dw.ads_case_loop +3）：`product_type` text、`repayment_records` jsonb、`create_time` timestamptz。
**理由**：与现有 14 列风格对齐；char(1) 沿用 `call_ended`/`has_high_alert` 的既定风格（禁 boolean）；JSONB 沿用 `repayment_records` 语义。
**备选**：boolean → 否决，与现有快照列 `call_ended`/`has_high_alert` 的 char(1) 约束不一致。

### D3：PII 处理 — phone 走 crypto.py AES-256-GCM + SHA-256，id_card 仅 SHA-256

**选择**：phone 用 `services/complaint-service/.../crypto.py` 已有 `encrypt()`（AES-256-GCM）加密并算 SHA-256 哈希，落 `phone_encrypted`+`phone_hash`；id_card 仅 SHA-256 哈希落 `id_card_hash`；明文即弃，不入日志。
**理由**：复用现有 `crypto.py`，满足 PII 硬约束（手机号/身份证/银行卡返回智能体前必须脱敏）。
**备选**：对称可逆加密 id_card → 否决，身份证只需"不可逆+可比对"，无需解密。
**自检**：脚本写完后 grep 明文样例、重算 ISO 7064 MOD-11 校验位、count 对账。

### D4：批量生成用 seed 化随机 + 场景骨架×变体池

**选择**：`--seed 42` 驱动 `random.Random(seed)`，案件 20 字段由随机槽位派生（principal 1万~50万、overdue_days 1~360、account_stage 由 overdue_days 派生、gender 由身份证第 17 位派生）；通话由"场景骨架（合规/客户异常/坐席违规三骨架）×话术变体池 × 随机槽位 × 可选分支"组合，生成后 SHA-256 去重。
**理由**：seed 化保证可重复；场景骨架保证分布（60/25/15）与违规可命中 qc-service 现有规则 R11/R12/R13。
**备选**：纯随机 → 否决，无法保证分布与违规命中。

### D5：订阅器泛化（G2）— 去掉 SEED_CASE_ID 硬编码，保留在途/已处理守卫

**选择**：删除 `subscriber.py#L36-L37` 的 `SEED_CASE_ID` 常量与"只处理该 case_id"判定；改为对所有 `qc.check.completed` payload 的 case_id 一律走"查 qc_records 已落库 → POST 创建 → POST judge → PUT review"。保留现有 `_graph_inflight` 在途登记与 `_resume_locks` 防并发重入（complaint-service-graph-fix 已验证）。
**理由**：在途守卫已在上一个 change 验证，本 change 只需把"单 case 过滤"改成"全 case 通行"，不破坏幂等。
**备选**：新建 case_id 白名单 → 否决，与"批量"目标相悖。

### D6：投诉正文差异化（G3）— 由 qc_violations + payload risk/grade 派生模板

**选择**：订阅器收到 `qc.check.completed` 后，按 session_id 查 `qc_violations` 全量违规记录（含 `speaker`/`redline_tag`），按 speaker 映射到三类正文模板（坐席违规=agent / 客户侧异常=user / 干净合规=无违规），填入 case-specific 槽位（客户名脱敏值、违规项中文摘要、overdue_amount 等）；payload 的 `risk`/`grade` 作为一致性校验信号。
**理由**：满足"按实际违规项组装差异化 complaint_text"；`qc_alerts` 仅含 agent high 违规（`is_alert_eligible` 过滤 speaker='agent' AND severity='high'），无法区分客户侧异常，`qc_violations` 含全量违规记录与 speaker 字段，是正确数据源；两者均为 qc-service 已落库表，不引入新依赖。
**备选**：调 LLM 生成正文 → 否决，成本高且引入新失败面；模板化足够。

### D7：judge 跑图限量 — 投诉创建全量洪峰，judge 仅约 20 单

**选择**：订阅器创建投诉可全量（200 单），但 judge 调用走有界子集（约 20 单，由 `--judge-budget` 或常量控制），剩余投诉停在"已创建未研判"状态。
**理由**：LLM QPS 与成本是硬瓶颈（platform/llm-reliability 的限流+熔断+有界重试已就绪）；judge 是 LLM 重操作，全量 200 单跑图成本不可控。
**备选**：全量 judge → 否决，成本与 LLM 限流冲突。
**遗留**：剩余 180 单的研判留给后续 change（本 change 仅验证零件在压力下可用）。

### D8：kill 消费者丢任务证据 — 可控 kill + 重启补投

**选择**：批量流量跑到一半时 `kill -TERM` 消费者进程，等待数秒后重启，观察丢任务是否被补投或入 `csmp:backflow:retry`，记录证据（丢任务数、补投数、最终对账是否平衡）。
**理由**：为 P5 Stream 升级（Redis Stream 替代 List）拿真证据，证明当前 List 模式在 kill 下会丢任务。
**备选**：模拟网络分区 → 否决，kill 进程更直接、更可复现。
**安全**：kill 前记录在途 case_id 集合，重启后比对，确保无不可恢复脏数据。

### D9：business_ticket 桥 mapper 从快照读新列

**选择**：`business_ticket_bridge.py` 的 `query_case_info` mapper 改为从快照行的 `product_type`/`repayment_records`/`create_time` 三列读实际业务值，不再固定填空串/空列表；仅当快照行对应列为 NULL 时返回空。读端返回形状仍对齐现有 fixture。
**理由**：spec MODIFIED 已约束行为（见 `specs/complaint-service/mcp-real-path`），本决策仅记录实现落点与读端形状对齐，避免桥直接查 `public.cases` 破坏"桥走快照"既有约定。
**备选**：桥直接查 `public.cases` → 否决，违反 mcp-real-path 既有"桥走快照接口"约定。

## Risks / Trade-offs

- [迁移在生产 cases 表加列可能锁表] → 全部 ADD COLUMN NULL/DEFAULT，PostgreSQL 11+ 仅元数据锁，秒级完成；staging 先验证；低峰执行。
- [批量生成 id_card 校验位重算失败] → 自检阶段重算全部，失败则脚本退出非零并回滚事务，不留半截数据。
- [kill 消费者后部分任务永久丢失] → kill 前快照在途 case_id 集合，重启后比对补投；最终对账不平衡则记为已知阻断，不掩盖。
- [judge 20 单以外投诉停在"已创建未研判"] → 本 change 验收只要求 20 单子集两路回流齐全，其余投诉的回流留给后续 change；验收报告中明确标注子集边界。
- [批量 judge 触发 LLM 限流/熔断] → 这正是本 change 要验证的零件；熔断 open→half-open→closed 恢复是验收项之一，非阻断。
- [repayment_records JSONB 形状不一致] → 生成端固定 0~5 条结构化 JSONB，桥读端允许空数组，形状对齐 fixture。

## Migration Plan

1. **staging 验证迁移**：在 staging 跑 0014 up/down 各一次，确认 cases +10 列、dw.ads_case_loop +3 列、downgrade 干净。
2. **生产执行迁移**：低峰期 `migrate upgrade`，确认表结构 17 列（快照）/ 10 新列（cases）。
3. **批量生成**：依次跑 `gen_cases_batch.py --count 200 --seed 42` → `gen_calls_batch.py --count 200 --seed 42`，自检全过。
4. **泛化订阅器**：部署 G2/G3 改动，重启 data-platform。
5. **批量流量**：发布 `etl.task.done`，触发批量质检→批量 `qc.check.completed`→批量创建投诉→judge 20 单子集→review approve→两路回流。
6. **kill 消费者证据**：跑到一半 kill，重启，记录丢任务补投情况。
7. **对账验收**：跑批量对账脚本，输出推送/消费/落库/重复/回流矩阵 + 阻断清单。
8. **回滚策略**：迁移 downgrade、订阅器回滚到上一版本、清理批量生成的 200 案件/通话（按 seed 42 可重算 case_id 集合删除）。

## Open Questions

- judge 有界子集的具体数量（20 还是其它）— 可在 tasks 执行时按 LLM 限流实际承载微调，不影响 spec 行为。
- kill 消费者的具体时机（流量跑到 50% 还是 70%）— 可在执行时按场景选择，不影响验收矩阵结构。

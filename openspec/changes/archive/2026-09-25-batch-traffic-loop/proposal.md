## Why

complaint-service-graph-fix 已让单案件真路径 13/13 PASS，但链路只覆盖一单。限流、熔断、幂等、队列、三级超时等零件在真实压力下是否可用尚未验证；同时快照表缺 `product_type`、`repayment_records`、`create_time` 三列导致 business_ticket 桥读快照时这三个字段恒空，订阅器硬编码单个 `SEED_CASE_ID` 无法处理批量案件，投诉正文千人一面无法反映各案实际违规项。现在做是为了把链路从"单案件"扩展到"200 案件 + 200 通话"批量流量，在真压力下暴露并修复上述缺口。

## What Changes

- 迁移 0014：`public.cases` 新增 10 列（`case_source`、`gender` CHAR(1)、`product_type`、`principal`、`interest_rate`、`loan_date`、`due_date`、`overdue_date`、`repayment_records` JSONB、`stop_call_status` 默认 callable）；`dw.ads_case_loop` 新增 3 列（`product_type`、`repayment_records` JSONB、`create_time`）。全部可空/带默认，不碰现有行，downgrade 可回滚。
- business_ticket 桥 mapper（`services/complaint-service/.../business_ticket_bridge.py`）：从快照读这 3 个新值，不再固定填空串/空列表；数据中台预处理时同步写入快照表这 3 列。
- 新增 `services/data-platform/scripts/gen_cases_batch.py`：支持 `--count 200 --seed 42`，生成 20 字段契约的差异化案件，phone 经 AES-256-GCM 加密 + SHA-256 哈希、id_card 仅 SHA-256 哈希、明文即弃；幂等 upsert、单事务、重跑不重复，含自检（号码正则、身份证 ISO 7064 MOD-11 校验位重算、零明文 PII、count 一致）。
- 新增 `services/data-platform/scripts/gen_calls_batch.py`：支持 `--count 200 --seed 42`，主表字段与现有 `call_records` 对齐（agent_id 可空），明细与 `dialog_turns` 对齐（speaker CHECK `('ai','user')`）。200 通各不相同（场景骨架×话术变体池×随机槽位×可选分支，哈希去重）；分布约 60% 干净合规 / 25% 客户侧异常 / 15% 坐席违规；`call_status` 约 85% completed，其余 no_answer/failed/短通话；轮次 2~12 不等；通话时间落在案件创建后的随机时点。
- 链路泛化 G2：data-platform 订阅器（`services/data-platform/.../subscriber.py`）去掉硬编码 `SEED_CASE_ID=11111111-...501`，泛化处理全部案件，保留幂等与在途守卫。
- 链路泛化 G3：订阅器推送投诉时按各案实际命中的违规项组装差异化 `complaint_text`，不再千人一面。
- 批量全链路跑通 + 对账：推送数 = 消费数 = 落库数；零重复工单；快照质检列 + 投诉列两侧回流齐全。投诉创建可全量洪峰，judge 跑图放有界子集（约 20 单，受 LLM 瓶颈与成本约束）。中途 kill 消费者进程制造丢任务场景，为 P5 Stream 升级拿证据。验证限流命中、熔断 open→half-open→closed 恢复、队列排空。

## Capabilities

### New Capabilities
<!-- 本 change 不引入全新能力，全部为现有能力的批量扩展与缺口修复 -->
（无）

### Modified Capabilities
- `data-platform/min-loop`: 从单案件种子扩展到批量流量生成（案件 20 字段契约 + 通话对齐现有表结构 + PII 加密/哈希）、订阅器泛化处理全部 case_id（G2）、投诉正文按实际违规项差异化组装（G3）、批量对账验收（推送数=消费数=落库数、有界 judge 子集、kill 消费者丢任务证据、限流/熔断/队列验证）、快照表新增 `product_type`/`repayment_records`/`create_time` 三列
- `complaint-service/mcp-real-path`: business_ticket 桥 `query_case_info` mapper 从快照读 `product_type`、`repayment_records`、`create_time` 三个新值，不再固定填空串/空列表，返回形状仍对齐现有 fixture

## Impact

- **数据库迁移**：新增 `migrations/0014_*.sql`（cases +10 列、dw.ads_case_loop +3 列），downgrade 路径必须可回滚；staging 验证后再上生产（项目硬约束）。
- **代码**：
  - `services/data-platform/scripts/gen_cases_batch.py`（新增）
  - `services/data-platform/scripts/gen_calls_batch.py`（新增）
  - `services/data-platform/.../subscriber.py`（G2 去硬编码、G3 差异化正文）
  - `services/data-platform/...` 预处理与快照写入同步落 3 个新列
  - `services/complaint-service/.../business_ticket_bridge.py`（mapper 读快照新列）
- **依赖与系统**：复用已有 `crypto.py`（AES-256-GCM `encrypt()`）、Redis 3 号库事件总线、data-platform 快照 GET/PUT、qc-source/pending-qc/health 接口；不新增任何对外接口，不碰 api-gateway 对外路由。
- **不碰范围**：specs 00~07、qc-service 生产代码、rag-service 生产代码、现有测试用例；禁止手插 qc_records、禁止造假回写、禁止 git commit（完成后等待人工审查）。
- **风险**：批量 judge 受 LLM QPS 与成本约束需限量；kill 消费者制造丢任务场景需可控、可恢复。

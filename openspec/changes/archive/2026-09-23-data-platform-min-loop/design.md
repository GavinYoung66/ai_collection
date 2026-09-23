## Context

三块系统各自能跑：qc-service 通过 Redis 订阅 `etl.task.done` 开检、发布 `qc.check.completed`；complaint-service 通过 `POST /api/v1/complaints` 创建工单、走研判图（图节点调 `mcp.call`）；rag-service 提供 `POST /api/v1/kb/search`。但三块没有用同一套数据串过：投诉回写打 Mock、研判走 Mock MCP、质检完成后投诉读不到结果。现有约束：`data-platform MUST NOT HTTP 调用 qc`（qc-service/events spec）、`complaints` 表已有 `case_id` 字段（complaint-service/data-layer spec）、`qc.check.completed` payload 已含 `case_id`（qc-service/events spec）。

## Goals / Non-Goals

**Goals:**
- 同一 case_id 在种子→质检→投诉→快照四处对得上
- 质检真开检（不是人手插 qc_records）
- 投诉研判走真 RAG（USE_MOCK_MCP=false 时）
- 回写真写快照（不是 Mock）
- 中台尽量薄：只加一张表 + 一个只读接口 + 种子/验收脚本

**Non-Goals:**
- 不建数仓分层、不做 ETL 流水线
- 不改 qc-service / rag-service 的 spec
- 不改投诉图节点、sample_backflow、evidence_call_qc、complaint_service.create
- 不做前端、不做 MCP 网关、不做 Redis Stream 改造
- 不改 openspec/specs/00～07

## Decisions

### D1: 种子 upsert 用 ON CONFLICT 或 DELETE+INSERT

种子脚本对三张业务表 + 快照表用 upsert。选用 ON CONFLICT(case_id) DO UPDATE（PostgreSQL 原生），如果主键不是 case_id 则 fallback 到 DELETE WHERE case_id 再 INSERT。两种方式都保证重复执行不产生第二套主键。不建独立幂等表。

**列名已对齐现有 DDL**：`cases` 主键是 `id`（UUID），没有 `case_id` 列。闭环固定 `cases.id = 11111111-2222-4333-8444-555555555501`。必填列包括 `case_number`、`customer_name`、`phone_encrypted`、`phone_hash`、`overdue_amount`、`overdue_days`、`account_stage`。`call_records.case_id` 外键指向 `cases.id`，`session_id` 固定 `min-loop-sess-001`。`dialog_turns.speaker` 只能是 `ai` 或 `user`。电话只写假占位，不写明文号码。

**替代方案**：用外部 complaint_id 跳过表（第一段提示词方案）——拒绝，因为多一张表违反"能不加的表都不加"。

### D2: etl.task.done 发布到 QC_REDIS_DB

data-platform 发布 `etl.task.done` 时，Redis 库号用 `QC_REDIS_DB`（默认 3），与 qc-service 已有订阅一致。不发明第二套 Redis 通道，不发布到 0 号库。payload 的 `backfill_date` 等于种子通话结束那天（Asia/Shanghai）。

**Redis 依赖（C2）**：data-platform 现有服务无 Redis 连接，实现发布器/订阅器前 SHALL 先为 data-platform 新增 Redis 连接配置，指向与 qc-service 同一 Redis 实例，库号 `QC_REDIS_DB`（默认 3）。SHALL NOT 另起一个 Redis 实例，SHALL NOT 发到 0 号库。

### D3: 订阅 qc.check.completed 触发推投诉，不轮询

data-platform 订阅 `qc.check.completed`（走 `QC_REDIS_DB`），收到事件后 HTTP 调用 `POST /api/v1/complaints`。不轮询 qc_records、不 sleep 等待。这保证"质检完成之后才研判"的顺序，避免投诉读到空质检。

**替代方案**：中台轮询 `GET /api/v1/qc/records/{session_id}` 直到有结果——拒绝，因为引入轮询复杂度和超时问题。

### D4: 投诉创建只传 complaint_text + case_id

POST 体最小化：只带 `complaint_text` 和 `case_id`。不传通话 `session_id`（该字段在 complaints 表是研判会话号，填了会和通话号混）。接受 `complaint_id == case_id` 的现有行为，不做幂等表，不改 `complaint_service.create`。

### D5: 进程内桥不改图节点，改 RealMCPTransport 分发

三个桥（business_ticket / call_qc / data_platform）在 complaint-service 进程内实现，图节点仍只调 `mcp.call`。实现方式 SHALL 改 `RealMCPTransport.call()` 的分发逻辑：为这三个 server 新增进程内桥分支，替代现有 `_forward_gateway`（现有实现对非 rag_kb 工具一律转发 `MCP_GATEWAY_URL`）。改完后这三个 server 不要求 `MCP_GATEWAY_URL`，不抛 `MCP_GATEWAY_URL missing`。桥的形状对齐现有 fixture：
- `business_ticket.query_case_info`：GET /api/v1/data/snapshot/{case_id} → 映射到 fixture 字段；快照查不到时 fallback 读 `public.cases` 同一行
- `call_qc.query_call_asr`：快照查 session_id → GET qc-source → 组装 fixture 形状
- `call_qc.query_qc_violations`：快照查 session_id → GET /api/v1/qc/records/{session_id} → 列表
- `data_platform.write_session/write_human_review_truth`：研判 session_id 反查 complaint_id + case_id → 调 PUT 内部回写口更新快照投诉列

所有桥查不到数据时返空列表，不返 500。`rag_kb` 桥已存在，不改。

### D6: 快照表 dw.ads_case_loop 14 列（英文列名）+ case_id 唯一索引

一张表，14 列，英文列名（不新造字段，质检列沿用 qc_records 库列名）：

| 英文列名 | 类型 | 说明 |
| :--- | :--- | :--- |
| `case_id` | text/varchar | 唯一索引，一行一案件 |
| `customer_name` | text | 客户名脱敏（如「张*」） |
| `overdue_amount` | numeric | 欠款，对齐 public.cases |
| `overdue_days` | int | 逾期天数，对齐 public.cases |
| `session_id` | text | 通话 session_id，供桥反查 |
| `call_ended` | char(1) | `0` 或 `1`，禁止 boolean |
| `qc_record_id` | text | = qc_records.id（对外主键名） |
| `compliance_score` | 沿用 qc_records | 质检分数（库列名，非 score） |
| `risk_level` | 沿用 qc_records | 风险（库列名，非 risk） |
| `has_high_alert` | char(1) | `0` 或 `1`。收到事件后按 session_id 查 `qc_alerts.level=high`，禁止 boolean |
| `complaint_id` | text/uuid | 本闭环等于 case_id |
| `verdict` | text | 研判结论（write_session 入参 verdict） |
| `task_status` | text | 任务状态 |
| `backflow_at` | timestamptz | 最后回流时间，Asia/Shanghai |

字段映射（事件 JSON → 库列）：
- `qc.check.completed` 的 `score` → `compliance_score`、`risk` → `risk_level`、`grade` 不落库
- `has_high_alert` 由 qc_alerts 是否含 level=high 判定
- 库列名固定 `compliance_score`/`risk_level`；事件/HTTP 键名按 qc-service data-layer spec（事件用 `score`/`risk`，HTTP 用 `compliance_score`/`risk`）

两路回流：
- data-platform 订阅 `qc.check.completed` → 写质检列（compliance_score/risk_level/has_high_alert）
- complaint-service 回写桥 → 写投诉列（complaint_id/verdict/task_status）

重复投递只覆盖（ON CONFLICT(case_id) DO UPDATE），不插第二行。

### D7: 快照接口（只读 GET + 内部回写 PUT）

data-platform 提供两个接口：
- 只读 `GET /api/v1/data/snapshot/{case_id}`：返回快照行，供桥反查 session_id
- 回写 `PUT /api/v1/data/snapshot/{case_id}`：body 只含 `complaint_id`/`verdict`/`task_status`。不加入 api-gateway 对外路由，不新做登录。本机闭环可以调用

响应均沿用统一格式 `{code, message, data, trace_id}`，不新增错误码段。内部 HTTP 沿用现有超时（5 秒）与重试（3 次）。

### D8: USE_MOCK_MCP 进程级开关不变

`USE_MOCK_MCP` 保持 complaint-service 进程级开关，默认 true。闭环验收时整个进程切 false。不实现独立 MCP 网关，不要求 `MCP_GATEWAY_URL`。

### D9: 可观测性与超时处理（覆盖"真推送/真消费/真检索/真回流"四件事的失败可见性）

- **真推送可观测（O1）**：发布 `etl.task.done` 后记日志（case_id、session_id、backfill_date、发布时间）；发布失败留下可查记录，不假装成功
- **真消费超时告警（O2）**：种子入库后 N 分钟（建议 10min）内未收到匹配 case_id 的 `qc.check.completed`，记告警日志（含 case_id、等待时长），不无限阻塞
- **真检索降级验证（O3）**：验收时模拟 rag-service 检索失败，确认研判日志含降级标记（`[规则兜底]` 前缀或降级事件），进程不崩
- **真回流完整性校验（O4）**：验收要求快照行质检列和投诉列都有值；只有一侧成功时记告警，验收不通过

## Risks / Trade-offs

- **[已核实] complaint_id == case_id** → 已读 `complaint_service.create` 源码确认：`cid = case_id or uuid4()`，传入 case_id 时 complaint_id == case_id。重复 POST 靠 `session_service.create_session` 的幂等映射（SETNX + 反查）不产生第二张工单，成立
- **[已核实] POST /api/v1/complaints 支持 case_id** → `ComplaintCreateRequest` 已含 `complaint_text/session_id/case_id/channel`，case_id 受支持
- **[已核实] 回流在人审之后** → `judge(run_graph=true)` 停在 `human_review`，`sample_backflow` 只有 `Command(resume="approve")` 之后才会跑。现有 `PUT /review` 不恢复图，且 judge 里的 MemorySaver 随请求结束丢弃。本 change 不改图节点、不新做路由：judge 的 checkpointer 按 complaint_id 留在投诉进程内；已有 `PUT /review` 在 `review_action=approve` 时对同一线程 resume，然后才进入 `sample_backflow`
- **[风险] 快照反查 session_id 时快照行还没写好** → 种子脚本先写快照空行（含 session_id），保证桥反查时数据已就位
- **[风险] 回写失败导致快照行不完整** → 沿用投诉已有重试 3 次 + `csmp:backflow:retry`，快照行保持部分列空，不阻塞工单
- **[风险] 知识库没有可被 search_regulations 命中的样例** → 验收前用 qc-service 已有禁语字典或 rag-service 已有样例确认；不新做解析器
- **[风险] 三张业务表确切列名未在本 change 冻结** → apply 第一步先定位 public.cases/call_records/dialog_turns 的 DDL，再写种子脚本（见 D1）

## Migration Plan

1. 部署快照表迁移（CREATE TABLE dw.ads_case_loop）
2. 部署 data-platform 只读接口
3. 部署 complaint-service 进程内桥（USE_MOCK_MCP=true 时不影响现有路径）
4. 运行种子脚本灌入假数据
5. 发布 etl.task.done 触发质检
6. 质检完成后推投诉
7. 闭环验收（USE_MOCK_MCP=false）
8. 确认旧测试仍绿（USE_MOCK_MCP=true）

**回滚**：删除快照表、移除桥实现、恢复 USE_MOCK_MCP=true。种子数据可重复执行 upsert 覆盖，不需要清理。

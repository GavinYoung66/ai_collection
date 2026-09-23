## 1. 假数据种子写入已有三张业务表 + 快照空行（约 2 小时）

- [x] 1.1 创建 `data-platform/scripts/seed_min_loop.py`。`cases.id` 固定 UUID `11111111-2222-4333-8444-555555555501`（表无 case_id 列）。写入必填列：`case_number=MIN-LOOP-001`、客户名「张*」、假 `phone_encrypted`、64 位假 `phone_hash`、固定欠款与逾期天数、`account_stage=M1`。禁止明文手机号。验证：连续两次执行，该 UUID 只有一行
- [x] 1.2 同脚本 upsert `call_records`（`session_id=min-loop-sess-001`，`case_id` 为该 UUID，`call_type=ai`，`call_status=completed`，`end_time` 为 Asia/Shanghai 当天，`duration_seconds` > 0）和 `dialog_turns`（`speaker` 只能是 `ai` 或 `user`，坐席高危话用 `ai`，能命中 R11/R12/R13）。验证：不产生第二套 session_id，插入 `speaker=agent` 会失败故不得使用
- [x] 1.3 同脚本 upsert `dw.ads_case_loop` 空行（`case_id` 为该 UUID，`call_ended='1'`，类型 char(1) 不是 boolean；质检列与投诉列先空）。验证：只有一行，`call_ended` 为 `1`
- [x] 1.4 脚本失败时整体重跑，不留半截数据。验证：中途失败后重跑，四张表数据一致
- [x] 1.5 **禁止改动**：qc-service 代码（质检服务不得 INSERT call_records）、complaint-service 代码、rag-service 代码、openspec/specs/00～07

## 2. 发布带 backfill_date 的 etl.task.done，质检真开检（约 2 小时）

- [x] 2.1 在 data-platform 实现 `etl.task.done` 发布器，Redis 库号用 `QC_REDIS_DB`（默认 3），payload 含 `backfill_date` 等于种子通话结束那天。验证：发布后用 `redis-cli -n 3 SUBSCRIBE etl.task.done` 能收到消息
- [x] 2.2 发布 etl.task.done 后记日志（含 case_id、session_id、backfill_date、发布时间），发布失败留下可查记录。验证：日志能查到该次发布记录
- [x] 2.3 确认 qc-service 已有订阅 `etl.task.done` 的机制正常工作（GET pending-qc + qc-source 走离线质检），`QC_AUTO_COMPLEMENT_AFTER_ETL` 保持默认 true。验证：qc_records 表出现该 session_id 的质检记录，质检分数来自真开检
- [x] 2.4 **禁止改动**：qc-service 订阅代码、qc-service spec、QC_AUTO_COMPLEMENT_AFTER_ETL 默认值、不 HTTP POST 调用 qc、不发布到 0 号库

## 3. 质检完成后再推投诉（约 2 小时）

- [x] 3.1 在 data-platform 实现 `qc.check.completed` 订阅器，Redis 库号用 `QC_REDIS_DB`（默认 3）。验证：用 `redis-cli -n 3 PUBLISH qc.check.completed` 发测试消息，订阅器能收到
- [x] 3.2 收到 `qc.check.completed` 且 case_id 匹配种子案件后，HTTP 调用已有 `POST /api/v1/complaints`，POST 体只带 `complaint_text` 和 `case_id`。验证：投诉工单创建成功，返回 complaint_id
- [x] 3.3 确认不传通话 session_id 到投诉创建请求的 session_id 字段（该字段是研判会话号）。验证：POST 请求体不含 session_id
- [x] 3.4 确认重复 POST 同一个 case_id 不产生第二张工单（靠现有行为，不做幂等表）。验证：连续两次 POST，投诉表只有一张该 case_id 的工单
- [x] 3.5 触发研判分三步，不改图节点、不新做路由：POST 创建工单后 POST `/{complaint_id}/judge` body `{"run_graph": true}`，图停在人审且尚未 write_session。judge 的 checkpointer 按 complaint_id 留在投诉进程内，禁止请求结束就丢弃。然后 PUT 已有 `/{complaint_id}/review`，`review_action=approve`，对同一线程 `Command(resume="approve")`，进入 sample_backflow。验证：approve 之前快照投诉列为空，approve 之后 write_session 被调用
- [x] 3.6 确认不使用 `qc.complaint.suspected` 事件自动建投诉工单，该事件仅用于已在途投诉补证。验证：收到 qc.complaint.suspected 不触发 POST /api/v1/complaints
- [x] 3.7 实现超时告警：种子入库后 N 分钟（建议 10min）内未收到匹配 case_id 的 `qc.check.completed`，记告警日志（含 case_id、等待时长），不无限阻塞。验证：模拟 qc 不发事件，N 分钟后日志出现告警
- [x] 3.8 **禁止改动**：`complaint_service.create` 函数、投诉 API 路由、不另做幂等表

## 4. 投诉进程内桥：改分发 + 案件、qc-source 对话形状、质检记录、已有 RAG 桥、回写反查 case_id（约 2 小时）

- [x] 4.1 改 `RealMCPTransport.call()` 分发逻辑：为 business_ticket / call_qc / data_platform 三个 server 新增进程内桥分支，替代现有 `_forward_gateway`（非 rag_kb 一律转发 MCP_GATEWAY_URL）。验证：USE_MOCK_MCP=false 时这三个 server 不抛 MCP_GATEWAY_URL missing
- [x] 4.2 在 complaint-service 实现 `business_ticket` 桥：`query_case_info` 用 case_id GET /api/v1/data/snapshot/{case_id}，返回 fixture 形状（case_id、overdue_amount、overdue_days、customer_name、product_type、create_time；repayment_records 允许空数组），快照查不到 fallback 读 public.cases。验证：USE_MOCK_MCP=false 时调用返回快照数据
- [x] 4.3 `query_history_tickets` 和 `query_history_complaints` 没有历史时返回空列表，不返 500。验证：查不到历史时返回 []
- [x] 4.4 在 complaint-service 实现 `call_qc.query_call_asr` 桥：用 case_id 查快照得 session_id，再 GET 已有 qc-source，组装 fixture 形状（列表，元素含 call_session_id、call_duration>0、asr_text_with_timestamp 含 speaker 和中文 text）。查不到返空列表不 500。验证：返回对齐现有 fixture 的形状
- [x] 4.5 在 complaint-service 实现 `call_qc.query_qc_violations` 桥：用 case_id 查快照得 session_id，再 GET 已有 /api/v1/qc/records/{session_id}，返回列表。查不到返空列表不 500。验证：返回质检违规列表
- [x] 4.6 在 complaint-service 实现 `data_platform.write_session` / `write_human_review_truth` 桥：用研判 session_id 反查 complaint_id 和 case_id，再调 PUT /api/v1/data/snapshot/{case_id} 内部回写口更新快照投诉列（complaint_id、verdict、task_status）。失败重试 3 次 + csmp:backflow:retry，工单不变成 500。验证：快照行投诉列被更新
- [x] 4.7 确认 USE_MOCK_MCP=true 时现有投诉演示和单测走 Mock，不受本 change 影响。验证：现有投诉测试全绿
- [x] 4.8 确认 rag_kb 桥（search_regulations、search_response_templates）保持现状，打到 rag-service POST /api/v1/kb/search。验证：不改 rag_kb 桥代码
- [x] 4.9 **禁止改动**：投诉图节点（sample_backflow、evidence_call_qc）、complaint_service.create、rag-service 代码、qc-service 代码、USE_MOCK_MCP 开关语义

## 5. qc.check.completed 与回写更新同一快照行（约 2 小时）

- [x] 5.1 data-platform 订阅 `qc.check.completed` 后，用 payload 中的 UUID `case_id` 更新快照质检列。`score`→`compliance_score`，`risk`→`risk_level`，`grade` 不落库。`has_high_alert` 查该 `session_id` 的 `qc_alerts`：有 `level=high` 则为 `1`，否则 `0`（char(1)）。不给 qc 新增接口。验证：快照质检列有值，且 case_id 等于种子 UUID
- [x] 5.2 complaint-service 回写桥通过 PUT /api/v1/data/snapshot/{case_id} 内部回写口更新快照投诉列（complaint_id、verdict、task_status）。验证：快照行投诉列有值
- [x] 5.3 重复投递只覆盖、不插第二行（ON CONFLICT(case_id) DO UPDATE）。验证：快照表始终只有一行该 case_id
- [x] 5.4 回写失败时工单状态正常推进，数据入 csmp:backflow:retry，快照行投诉列保持空。验证：回写失败不阻塞工单
- [x] 5.5 提供 `GET /api/v1/data/snapshot/{case_id}` 只读接口（供桥反查 session_id），返回快照行，响应沿用统一格式 {code,message,data,trace_id}。验证：GET 返回 14 列数据
- [x] 5.6 提供 `PUT /api/v1/data/snapshot/{case_id}`，body 只含 complaint_id/verdict/task_status，只更新该 case_id 行。不加入 api-gateway 对外路由，不新做登录。验证：本机 PUT 成功，网关配置里没有这条 PUT
- [x] 5.7 **禁止改动**：qc-service 代码（qc-service 里仍然禁止 ETL）、不新增报表 API、不新增错误码段、不新做登录、不把该 PUT 配进 api-gateway

## 6. 剧本验收 + 确认旧测试仍绿（约 2 小时）

- [x] 6.1 创建 `data-platform/scripts/run_min_loop.py`，顺序：种子 → etl.task.done → 等 qc.check.completed → 推投诉 → judge(run_graph=true) 停在人审 → PUT review approve → 确认快照两侧都有值。USE_MOCK_MCP=false。验证：脚本可重复执行
- [x] 6.2 验收前确认知识库里至少有一份仓库已有的法规/禁语短文能被 search_regulations 命中（用 qc-service 已有禁语字典或 rag-service 已有样例，不新做解析器）。验证：search_regulations 能命中该样例
- [x] 6.3 验收同一 case_id 在种子、qc_records、投诉工单、dw.ads_case_loop 四处对得上。验证：四处 case_id 一致
- [x] 6.4 验收质检分数来自真开检（不是人手插入 qc_records）。验证：qc_records 有该 session_id 的记录且分数非手写
- [x] 6.5 验收质检完成之后才创建投诉；人审 approve 之后投诉列才有值。验证：投诉创建时间晚于 qc.check.completed，且 approve 前投诉列为空
- [x] 6.6 验收 USE_MOCK_MCP=false 时法规来自 rag-service 真检索；失败走已有降级，进程不崩。验证：研判日志含 rag_kb 真调用
- [x] 6.7 验收降级路径真走过：模拟 rag-service 检索失败，确认研判日志含降级标记（`[规则兜底]` 前缀或降级事件），进程不崩。验证：模拟失败后日志出现降级标记
- [x] 6.8 验收两路回流都成功：快照行的质检列和投诉列都有值；只有一侧成功时记告警，验收不通过。验证：快照行两侧列均非空
- [x] 6.9 验收 USE_MOCK_MCP=true 的现有投诉测试、现有质检主路径、现有 RAG 主路径保持通过。验证：三个服务的现有测试全绿
- [x] 6.10 验收种子与快照中的电话、证件、卡号是假数据或脱敏值。验证：无明文 PII
- [x] 6.11 验收内部 HTTP 沿用现有超时（5 秒）与重试（3 次），失败有可查记录。验证：日志有超时/重试记录

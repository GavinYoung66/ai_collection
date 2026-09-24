## 1. 投诉持久化修复

- [x] 1.1 检查并修复 `complaint_service.create()` ORM 模型映射，使 case_id → complaints.case_id、complaint_text → complaints.complaint_text 正确写入。验证：POST /api/v1/complaints 后 SELECT * FROM complaints 能查到行
- [x] 1.2 检查并修复 session commit 逻辑，使创建成功后 commit 落库、失败回滚。验证：创建成功后 DB 有行，创建失败 DB 无行
- [x] 1.3 检查并修复重复 POST 同一 case_id 的幂等逻辑，使不产生第二张工单。验证：连续两次 POST 同一 case_id，SELECT count(*) = 1

## 2. 证据包组装与图启动修复

- [x] 2.1 检查并修复 `business_ticket_bridge.query_case_info` 返回值形状，使含 case_id/overdue_amount/overdue_days/customer_name/product_type/create_time。验证：USE_MOCK_MCP=false 时桥返回 fixture 形状
- [x] 2.2 检查并修复 `call_qc_bridge.query_call_asr` 返回值形状，使 speaker 映射 ai→agent、call_duration>0、asr_text_with_timestamp 含中文 text。验证：桥返回列表且元素字段完整
- [x] 2.3 检查并修复 `call_qc_bridge.query_qc_violations` 返回值形状，使经快照反查 session_id 再查 /api/v1/qc/records/{session_id}。验证：返回列表，查不到返空列表不返 500
- [x] 2.4 检查并修复 `rag_kb` 桥调 rag-service POST /api/v1/kb/search 返回值形状，使 verdict 含法规关键词。验证：USE_MOCK_MCP=false 时 verdict 含法规引用（注：本批仅交付桥返回值形状并运行时核对字段；端到端"verdict 含法规引用"受环境 KB regulation 分类为 OCR 脏数据、且 judge 设计停在 human_review 两因素限制，留待数据补种子 + 批次② review 后验证）
- [x] 2.5 检查并修复 `_judge_with_graph` 图启动逻辑，使 4 个工具调用顺序正确、返回值被正确组装为证据包。验证：POST /judge run_graph=true 返回 200 + thread_id，不返 500
- [x] 2.6 检查并确认 judge 停在 human_review 节点，使 write_session 未被调用。验证：judge 后快照投诉列仍为空

## 3. 图状态持久化与 review 恢复修复

- [x] 3.1 检查并修复图状态持久化逻辑，使 judge 创建的图状态在进程内保持到 review 恢复、thread_id=complaint_id。验证：judge 后用同一 thread_id 能恢复图
  - 修复：`_get_graph_checkpointer()` 进程级 MemorySaver（非请求级）；judge 与 review 均以 `thread_id=complaint_id` 编译图
  - 证据：测试 `TestBatch2GraphPersistence::test_thread_id_complaint_id_state_kept_and_resumed_to_backflow` / `test_service_resume_graph_thread_reuses_same_thread`；运行日志 `graph_resumed_after_approve`（进入断点成功）
- [x] 3.2 检查并修复 `PUT /review` review_action=approve 用同一 thread_id 恢复图，使图从 human_review 推进到 sample_backflow。验证：review 后图状态推进到 sample_backflow
  - 修复：`_resume_graph_thread` 复用同一进程级 checkpointer + thread_id；新增 G2a（先等同工单在途图结束）/G2b（精确判定 `next` 含 `human_review` 才 resume）/resume 互斥锁
  - 证据：运行日志 `graph_resumed_after_approve` `inflight_waited_s=18.4`、`entered_interrupt=true`，随后 `sample_backflow` 推进到 END
- [x] 3.3 检查并修复 review 恢复而非新建图的逻辑，使 write_session 被调用。验证：review 后 write_session 被调用（图执行了 sample_backflow 节点）
  - 证据：运行日志 `sample_backflow_start` → `backflow_write_ok(tool=write_session, attempt=1)` → `sample_backflow_ok`；`write_human_review_truth` 同样成功

## 4. 进程内桥分发修复

- [x] 4.1 确认 USE_MOCK_MCP=false 时 business_ticket/call_qc/data_platform 三 server 走进程内桥而非 gateway。验证：不抛 MCP_GATEWAY_URL missing
  - 归因订正：`RealMCPTransport.call()` 的四 server 分发在 data-platform-min-loop（e8a2d24）已存在，本 change 对该文件零改动；本项实际修复为 **thread_id 对齐**——judge 与 resume 两端 `thread_id` 由 sid/session_id 统一改为 complaint_id（`complaint_service.py` L532、L860），并新增 G1b `_graph_inflight` 在途守卫防并发重入，真路径下三桥才稳定被命中
  - 证据：judge 期间命中 BusinessTicketBridge、CallQcBridge、DataPlatformBridge，rag-service 收到 complaint-service 的 `POST /api/v1/kb/search`，全程无 `MCP_GATEWAY_URL missing`
- [x] 4.2 检查并修复桥调用失败时的容错逻辑，使返回空列表或默认值而非 500。验证：某桥调用失败时图继续执行不返 500
  - 证据：business_ticket 快照/DB 故障 → 默认零值 fixture（`case_snapshot_error_default_fixture`）；call_qc 快照/ASR/违规明细 5xx/超时 → 返空列表（`call_qc_snapshot_error` 等）；新增 `test_business_case_info_5xx_returns_default_fixture_never_raises`、`test_call_qc_transport_5xx_returns_empty_list_never_raises`；rag_kb 异常由 compliance 节点既有逻辑降级 `rag_result=[]` 继续判
- [x] 4.3 确认 review approve 后经 PUT /api/v1/data/snapshot/{case_id} 回写快照投诉列。验证：快照行 complaint_id/verdict/task_status 非空
  - 归因订正：`DataPlatformBridge.write_session` 的 PUT 回写能力在 e8a2d24 已存在，本 change 对该文件零改动；write_session 此前未触发的根因是 review 用错误线程恢复（thread_id=sid），随 **thread_id 对齐修复**（`complaint_service.py` L532、L860）后 approve 才能恢复同一线程进入 sample_backflow，write_session 才真正执行；节点侧 `_task_status_value` 保证 task_status 为枚举裸值
  - 证据：`backflow_write_ok` `case_id=11111111-2222-4333-8444-555555555501`；验收 6.8 PASS（`verdict=pending`）；`task_status` 枚举裸值 `resolved`（单测 `test_payload_task_status_uses_enum_bare_value`）
- [x] 4.4 确认回写失败 3 次后入 csmp:backflow:retry，工单不返 500。验证：工单状态正常推进
  - 归因订正：`DataPlatformBridge._enqueue_failed` 的"3 次失败或快照行缺失 → `LPUSH csmp:backflow:retry` + A5013、返回成功形状"逻辑在 e8a2d24 已存在，本 change 对该文件零改动；本项实际修复（N5）在 sample_backflow 节点侧——去除节点外层 3 次重试，重试只保留桥内单层，避免双层叠加变成每 tool 9 次 HTTP + 3 条重复 LPUSH
  - 队列归属：投诉服务**内部工作队列**（生产/消费均在本服务），走本服务 REDIS_DB（get_redis 配置驱动），非跨服务事件总线 3 号库

## 5. USE_MOCK_MCP=true 回归验证

- [x] 5.1 确认 USE_MOCK_MCP=true 时现有投诉/质检/RAG 主路径测试全绿。验证：pytest 三服务 tests/ 全通过
  - complaint-service：**338 passed**；qc-service：**371 passed, 2 skipped**；rag-service：**372 passed, 1 skipped**（验收脚本 6.9 实测全绿）
  - qc-service 修复内容：仅测试设施 `tests/conftest.py` autouse 增加 `reset_redis()`——根因为 resilience 全局 async Redis 客户端绑定首个 pytest 事件循环，跨 function 级事件循环复用触发 "Future attached to a different loop"（5 个失败同源）；未改 qc-service 生产代码

## 6. 闭环验收

- [x] 6.1 确认 conda ai_collection 环境有 greenlet。验证：python -c "import greenlet" 不报错
  - 证据：`greenlet ok 3.5.5`
- [x] 6.2 单独测 POST /judge run_graph=true 返回 200 + thread_id 且停在 human_review。验证：不返 500，快照投诉列为空，定位修复是否到位
  - 证据：`POST /judge 200 OK`；验收 `6.1-judge PASS`（judge 后停在 human_review）；`6.5-pre PASS`（approve 前快照投诉列为空 `complaint_id=None`）
- [x] 6.3 重跑 run_min_loop.py，确认 6.1~6.11 全 PASS。验证：验收对照表 11/11 pass
  - 证据：清基线重跑 `总计: 13/13 通过`（含 6.9 三服务测试 complaint 338→343 / qc 371 / rag 372；修复后 complaint 净增 N4/N6 共 5 个测试）
- [x] 6.4 确认连续两次执行 run_min_loop.py 不产生重复数据。验证：第二次执行 11/11 pass，DB 无重复行
  - 证据：清基线跑首跑 A（13/13）后**不清库**连续重跑 B（13/13）；qc_records=1、complaints=1、qc_violations=3、ads_case_loop=1 行数两次完全一致；脚本新增幂等重跑口径（qc 终态 PENDING_REVIEW 跳过事件等待、工单终态跳过 judge/review），日志 `幂等重跑：质检已终态...` / `幂等重跑：工单已终态...`
  - N4：`_resume_locks` 持锁末尾按 `_waiters` 回收，三测覆盖（单路/双路串行/50 工单零残留）
  - N5：sample_backflow 节点去外层重试，单层 3 次归 data_platform 桥（每 tool 3 请求、1 条 LPUSH），节点每 tool 仅 1 次调用
  - N6：双驱动落败路检测 `next 不含 human_review + human_review.review_action 已写` → `graph_resume_skip_already_resumed` 秒回，不再空轮询 120s（两测：落败路不 resume、挂起断点正常 resume）

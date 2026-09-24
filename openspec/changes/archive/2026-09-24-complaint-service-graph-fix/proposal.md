## Why

data-platform-min-loop 闭环验收时，complaint-service 在 `USE_MOCK_MCP=false` 下无法跑通完整链路：judge 返回 500、投诉未持久化到 PostgreSQL、review approve 后 write_session 未触发、快照投诉列仍空。根因是图证据包组装、进程级 checkpointer、进程内桥分发三处实现有缺陷，导致真路径下整个研判+回流链路断裂。需要修复后重跑 run_min_loop.py 验证 11/11 PASS。

## What Changes

- 修复 `_judge_with_graph` 证据包组装：三个 MCP 工具（business_ticket/call_qc/rag_kb）返回值形状对齐 fixture，图能正常启动并停在 human_review
- 修复投诉持久化：`complaint_service.create()` ORM 映射 + session commit 确保投诉写入 PostgreSQL
- 修复进程级 MemorySaver：按 complaint_id 保持 thread_id，review 时恢复同一线程执行 `Command(resume="approve")`
- 修复 review approve 后图进入 sample_backflow 节点并调 write_session 回写快照投诉列
- 修复 RealMCPTransport 进程内桥分发：USE_MOCK_MCP=false 时 business_ticket/call_qc/data_platform 三 server 正确分发
- 验证三个进程内桥返回 fixture 形状（business_ticket.query_case_info / call_qc.query_call_asr / data_platform.write_session）
- 修复后重跑 run_min_loop.py 验证 6.1/6.3/6.5/6.6/6.8 从 FAIL 转 PASS

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `complaint-service/mcp-real-path`: 修复进程内桥分发、证据包组装、图状态机、投诉持久化、MemorySaver 持久化、write_session 触发的 spec 级行为约束

## Impact

- 代码：`services/complaint-service/app/` 下 mcp/、service/、config/、api/ 目录
- 不改 data-platform / qc-service / rag-service
- 不改 openspec/specs/00~07
- 不改现有测试用例（USE_MOCK_MCP=true 主路径测试保持绿）
- 不改图节点结构、不新增路由

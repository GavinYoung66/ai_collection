## Context

data-platform-min-loop 闭环验收时 complaint-service 在 `USE_MOCK_MCP=false` 下链路断裂。三处实现缺陷：① judge 证据包组装时 MCP 工具返回值形状不匹配 fixture 导致 500；② 投诉创建未持久化到 PostgreSQL；③ 进程级 MemorySaver 未按 complaint_id 保持 thread_id，review 无法恢复同一线程。spec 级行为已在 `complaint-service/mcp-real-path` 定义，本设计解决实现层面的修复。

## Goals / Non-Goals

**Goals:**
- judge run_graph=true 成功启动图、停在 human_review、返回 thread_id
- 投诉创建写入 PostgreSQL complaints 表
- MemorySaver 按 complaint_id 持久、review 恢复同一线程进 sample_backflow
- write_session 被触发、快照投诉列非空
- run_min_loop.py 6.1/6.3/6.5/6.6/6.8 从 FAIL 转 PASS

**Non-Goals:**
- 不改图节点结构、不新增路由、不改 data-platform/qc-service/rag-service
- 不改 USE_MOCK_MCP=true 主路径测试
- 不新增外部 checkpointer 表（用进程内 MemorySaver）

## Decisions

### D1: 证据包组装修复 — 在桥层做形状适配

**决策**：在 business_ticket_bridge / call_qc_bridge 内部做返回值形状适配，确保输出对齐 fixture。不改图节点。

**理由**：图节点 `evidence_business` / `evidence_call_qc` 期望的 fixture 形状是固定的。桥层适配比改图节点风险低，且符合 spec "图节点 SHALL 仍只调 mcp.call"。

**替代方案**：改图节点适配新形状 → 否决（违反 spec 约束、风险高）。

### D2: 投诉持久化修复 — 检查 ORM 映射 + session commit

**决策**：检查 `complaint_service.create()` 的 ORM 模型映射，确保 case_id → complaints.case_id、complaint_text → complaints.complaint_text 正确写入。确认 session commit 在创建后执行。

**理由**：当前 POST 返回 200 但 DB 无行，最可能是 ORM 映射缺失或 commit 未执行。

### D3: MemorySaver 持久化 — 进程级字典按 complaint_id 存

**决策**：在 complaint-service 进程内维护一个 `Dict[complaint_id, MemorySaver]` 字典。judge 时创建 MemorySaver 并存入字典，thread_id = complaint_id。review 时从字典取出同一 MemorySaver 恢复图。

**理由**：spec 约束 "SHALL NOT 新增外部 checkpointer 表"。进程级字典满足闭环验收需求（单进程、不重启）。

**替代方案**：用 AsyncPostgresSaver → 否决（spec 约束 "SHALL NOT 新增外部 checkpointer 表"，闭环验收用进程级即可）。

### D4: review 恢复 — 用 thread_id 恢复而非新建图

**决策**：`PUT /review` 的 `review_action=approve` SHALL 用 judge 返回的 thread_id（= complaint_id）从 MemorySaver 恢复图，执行 `Command(resume="approve")`，图进 sample_backflow。

**理由**：当前 review 可能新建图而非恢复，导致 write_session 未触发。

### D5: write_session 触发 — sample_backflow 节点调 mcp.call

**决策**：确认 sample_backflow 节点代码调 `mcp.call("data_platform", "write_session", ...)`。RealMCPTransport 分发到 data_platform_bridge，桥经 PUT 回写快照。

**理由**：write_session 未触发可能是 review 未恢复同一线程（D4 修复后应解决），或 RealMCPTransport 未正确分发到 data_platform_bridge。

## Risks / Trade-offs

| 风险 | 缓解 |
|------|------|
| 进程级 MemorySaver 重启后丢失 | 闭环验收不重启 complaint-service；生产环境后续换 AsyncPostgresSaver |
| 桥层形状适配可能遗漏字段 | run_min_loop.py 6.6 验收 verdict 含法规关键词 |
| review 恢复可能找不到 thread_id | judge 返回 thread_id 后 data-platform 存储并传入 review |

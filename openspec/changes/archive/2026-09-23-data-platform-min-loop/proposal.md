## Why

三块已存在的系统（RAG 混合检索、合规质检、智能投诉研判）各自能跑，但没有一条真链路把它们用同一套案件数据串起来。质检开检后投诉读不到结果、投诉研判走 Mock 检索、回写打的是 Mock——数据中台作为薄底座缺一条最小闭环：真推送、真消费、真检索、真回流。本 change 不建新产品，只补薄底座和一张极简案件快照，让同一 case_id 在种子→质检→投诉→快照四处对得上。

## What Changes

- **新增 data-platform 最小闭环**：一个种子脚本（upsert 已有三张业务表 + 快照空行）、发布 `etl.task.done`（走 `QC_REDIS_DB`）、订阅 `qc.check.completed`（走 `QC_REDIS_DB`）触发 HTTP 推送投诉、一张快照表 `dw.ads_case_loop`（14 列，一行一 case_id）、一个只读接口 `GET /api/v1/data/snapshot/{case_id}`
- **投诉进程内桥补真桥**：为研判图真正调用的 `business_ticket`（query_case_info / query_history_tickets / query_history_complaints）、`call_qc`（query_call_asr / query_qc_violations）、`data_platform`（write_session / write_human_review_truth）三个工具补进程内真桥，打通到已有 HTTP 接口和快照表。`rag_kb` 桥已存在，不改
- **确认 qc 对 etl.task.done 的已有消费**：qc-service 已订阅 `etl.task.done` 并 GET pending-qc + qc-source 走离线质检。本 change 不改 qc-service spec，只在验收时确认这条链路在闭环中真跑通
- **不改现有测试**：`USE_MOCK_MCP=true` 时投诉/质检/RAG 主路径保持通过；闭环验收才切 `USE_MOCK_MCP=false`
- **不发明第二套**：不新做工单体系、不新造字段、不新开通道、不建 ODS/DWD/DWS、不做前端

## Capabilities

### New Capabilities

- `data-platform/min-loop`: 数据中台最小闭环——种子灌入已有三张业务表 + 快照空行、发布 etl.task.done、订阅 qc.check.completed 触发推投诉、dw.ads_case_loop 快照表与两路回流、GET /api/v1/data/snapshot/{case_id} 只读接口、验收脚本 run_min_loop.py

### Modified Capabilities

- `complaint-service/mcp-real-path`: 补三个进程内真桥（business_ticket / call_qc / data_platform）的 spec-level 行为——桥形状对齐现有 fixture、查不到返空列表不 500、回写反查 case_id 更新快照、USE_MOCK_MCP=true 不回归

## Impact

- **新增代码**：data-platform 下的种子脚本、快照表迁移、只读接口、etl.task.done 发布器、qc.check.completed 订阅器、验收脚本
- **修改代码**：complaint-service 的进程内桥；judge 的 checkpointer 按工单留在进程内；已有 PUT /review 在 approve 时恢复同一研判线程。不改图节点、不改 complaint_service.create、不改 sample_backflow、不新做路由
- **不改代码**：qc-service（仅确认 etl.task.done 消费链路）、rag-service（仅复用 POST /api/v1/kb/search）、complaint 图节点、sample_backflow、evidence_call_qc、complaint_service.create
- **现有测试**：USE_MOCK_MCP=true 路径不受影响
- **禁止修改**：openspec/specs/00～07 全局红线规范

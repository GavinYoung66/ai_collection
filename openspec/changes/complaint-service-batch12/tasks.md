# Tasks: complaint-service-batch12（模块14 外部依赖对齐）

依赖：本批**只写文档**，不写业务代码。路径钉死见 design D1。验证仅为 `test -f` + `grep` 列完备性；**禁止** `docker build` / `pytest` / 真实 MCP·LLM 流量作为本批完成条件。单元格允许 `TBD`。

## 14.0 目录与索引

- [x] 14.0.1 新建 `docs/complaint-service/external-deps/`，写入 `README.md`：列出六份交付物路径、14.1～14.4 对照、status 枚举（`not_started`/`in_progress`/`contract_confirmed`/`ready_for_switch`/`blocked`）、声明「TBD ≠ 已确认」、声明本批不实现真实 MCP Server、不切换批次11 stub 网关、编码侧继续 Mock。
  **验证**：`test -f docs/complaint-service/external-deps/README.md`；`grep -E 'mcp-server-tracking|mcp-contract-checklist|rag-kb-confirmation|llm-quota-confirmation|bizsys-integration-confirmation' docs/complaint-service/external-deps/README.md` 五路径均命中；`grep -E 'Mock|stub|TBD' docs/complaint-service/external-deps/README.md` 均命中。

## 14.1 MCP Server 进度跟踪与契约对齐（REQ-CJ-034）

- [x] 14.1.1 新建 `docs/complaint-service/external-deps/mcp-server-tracking.md`：4 Server × 14 tool 进度表。权威名 `business_ticket`/`call_qc`/`rag_kb`/`data_platform`；`prd_alias` 对照 business-ticket/call-qc/rag-kb/data-platform-writeback。14 tool 预填：`query_case_info`、`query_history_tickets`、`query_history_complaints`、`query_user_history_complaints`、`get_complaint_ticket`、`query_call_asr`、`query_qc_violations`、`query_call_sessions`、`search_regulations`、`search_response_templates`、`search_complaint_handling_guidelines`、`write_session`、`write_human_review_truth`、`push_rag_candidate`。表头列：`server`、`prd_alias`、`tool`、`owner_team`、`status`、`blocker`、`target_date`、`last_updated`。未知填 `TBD`。含「不实现真实 MCP / 不切 stub 网关 / 继续 Mock」声明。
  **验证**：`test -f docs/complaint-service/external-deps/mcp-server-tracking.md`；列名与 14 个 tool、四个权威名、`Mock`/`stub` 均 grep 命中（2026-09-06 已跑通）。

- [x] 14.1.2 新建 `docs/complaint-service/external-deps/mcp-contract-checklist.md`：14 tool 契约对齐清单。预填 `request_fields`/`response_fields`/`exception_class`；声明全部 tool `session_id_required`、统一信封 `{code, message, data}`、`pii_masked_at_server`；列 `mock_aligned`/`confirmed_by`/`confirmed_date`/`gaps`（后三列可 `TBD`）。本文件关闭模块 3.3/3.4 的契约确认，不改 MCP 代码。
  **验证**：文件存在；列名齐全；`grep -F '{code, message, data}'` 命中；14 个 tool 名均命中（2026-09-06 已跑通）。

## 14.2 rag-service 入库与质量确认（REQ-CJ-005）

- [x] 14.2.1 新建 `docs/complaint-service/external-deps/rag-kb-confirmation.md`：法规入库表 + 范本入库表 + 质量门槛表。门槛预填 top-3≥85%、召回≥90%、法规覆盖≥95%；实测与 dataset 可 `TBD`。写明阻塞影响与 Mock `rag_kb` 缓解；不改图节点。
  **验证**：文件存在；ingest/quality 列齐全；`top-3`/`召回`/`覆盖` 与 `85`/`90`/`95`、`Mock`/`rag_kb` 均命中（2026-09-06 已跑通）。

## 14.3 LLM 配额与连通确认（REQ-CJ-022/023）

- [x] 14.3.1 新建 `docs/complaint-service/external-deps/llm-quota-confirmation.md`：三模型表（DeepSeek `deepseek-r1`、DashScope `qwen-turbo`/`qwen-plus`），列齐全；千问 `unit_price_reasoning` 填 `N/A`；密钥「由部署/对接方线下确认」。专节 `reasoning_content` + `cot_encrypted`；声明 `USE_MOCK_LLM`。不改 LLM 代码。
  **验证**：三模型名、配额列、`reasoning_content`、`cot_encrypted`、`USE_MOCK_LLM`/`Mock LLM` 均命中（2026-09-06 已跑通）。

## 14.4 传统催收业务系统联调确认（REQ-CJ-015/038）

- [x] 14.4.1 新建 `docs/complaint-service/external-deps/bizsys-integration-confirmation.md`，含三节：工单推送 `POST /api/v1/complaints`；回调 `session_id`+`version`、10s/30s/60s、`csmp:callback:dlq`；复核六动作 + 四值 verdict 禁止 `normal` + 10 项必显。缓解脚本路径 `scripts/mock/push_complaint.sh`、`scripts/mock/push_batch_complaints.py`。不改 API。
  **验证**：POST 路径、字段、回调键、六动作、四值、`normal`/`必显`、两个 mock 脚本名均命中（2026-09-06 已跑通）。

## 14.5 负向收口（禁止改业务代码）

- [x] 14.5.1 确认本批 apply 未修改业务/部署代码：不得改 `services/complaint-service/`、`docker-compose*.yml`、`Dockerfile`、`.github/`。交付仅 `docs/complaint-service/external-deps/` 下上述六文件（另含本 change 的 OpenSpec 规划产物）。
  **验证**：六文档路径均存在；`git diff --name-only -- services/complaint-service docker-compose.dev.yml docker-compose.complaint.yml .github` 对本批为空（2026-09-06 已确认）。

# Tasks: complaint-service-batch12（模块14 外部依赖对齐）

依赖：批次0～11 已归档。本批**只写文档**，不写业务代码、**不改** `config/*.env`。路径钉死见 design D1。

**完成标准**：六份文档落档 + 表头行/关键串可 grep（未知可 `TBD`）。**不等于**外部全绿或已切真流量。

验证：`test -f` + **表头行** `grep -E '^\|'` + 关键串；**禁止** `docker build` / `pytest` / 真实 MCP·LLM 流量。

## 14.0 目录与索引

- [x] 14.0.1 新建 `docs/complaint-service/external-deps/`，写入 `README.md`：列出六份交付物路径、14.1～14.4 对照、status 枚举（`not_started`/`in_progress`/`contract_confirmed`/`ready_for_switch`/`blocked`）、声明「TBD ≠ 已确认」、声明本批不实现真实 MCP Server、不切换批次11 stub 网关、**不改** `config/*.env`；并写明：批次11 pre/prod 已写 `USE_MOCK_*=false` ≠ 外部已就绪，切真流量另开变更；dev/test 与本地在外部未就绪前继续 Mock。
  **验证**：`test -f docs/complaint-service/external-deps/README.md`；`grep -E 'mcp-server-tracking|mcp-contract-checklist|rag-kb-confirmation|llm-quota-confirmation|bizsys-integration-confirmation' docs/complaint-service/external-deps/README.md` 五路径均命中；`grep -E 'Mock|stub|TBD|USE_MOCK|不改.*env|env' docs/complaint-service/external-deps/README.md` 命中边界声明（至少含 Mock、stub、TBD，以及 USE_MOCK 或不改 env 之一）。

## 14.1 MCP Server 进度跟踪与契约对齐（REQ-CJ-034 / DD-05 §4.4）

- [x] 14.1.1 新建 `docs/complaint-service/external-deps/mcp-server-tracking.md`：4 Server × 14 tool 进度表。权威名 `business_ticket`/`call_qc`/`rag_kb`/`data_platform`；`prd_alias` 对照 business-ticket/call-qc/rag-kb/data-platform-writeback。14 tool 预填：`query_case_info`、`query_history_tickets`、`query_history_complaints`、`query_user_history_complaints`、`get_complaint_ticket`、`query_call_asr`、`query_qc_violations`、`query_call_sessions`、`search_regulations`、`search_response_templates`、`search_complaint_handling_guidelines`、`write_session`、`write_human_review_truth`、`push_rag_candidate`。Markdown 表头行须含列：`server`、`prd_alias`、`tool`、`owner_team`、`status`、`blocker`、`target_date`、`last_updated`。未知填 `TBD`。含「不实现真实 MCP / 不切 stub / 不改 env / 继续 Mock」声明。
  **验证**：`test -f docs/complaint-service/external-deps/mcp-server-tracking.md`；`grep -E '^\| *server *\| *prd_alias *\| *tool *\| *owner_team *\| *status *\| *blocker *\| *target_date *\| *last_updated *\|' docs/complaint-service/external-deps/mcp-server-tracking.md` 命中；对 14 个 tool 名逐个 `grep -q` 均命中；`grep -E 'business_ticket|call_qc|rag_kb|data_platform' docs/complaint-service/external-deps/mcp-server-tracking.md` 四个权威名均命中；`grep -E 'Mock|stub' docs/complaint-service/external-deps/mcp-server-tracking.md` 命中。

- [x] 14.1.2 新建 `docs/complaint-service/external-deps/mcp-contract-checklist.md`：14 tool 契约对齐清单。按 PRD §4.4 预填 `request_fields`/`response_fields`/`exception_class`；声明全部 tool `session_id_required`、统一信封 `{code, message, data}`、`pii_masked_at_server`；列 `mock_aligned`/`confirmed_by`/`confirmed_date`/`gaps`（后三列可 `TBD`）。本文件关闭模块 3.3/3.4 的契约确认，不改 `mcp-client` 代码。
  **验证**：`test -f docs/complaint-service/external-deps/mcp-contract-checklist.md`；`grep -E '^\| *request_fields *\| *response_fields *\| *exception_class *\|' docs/complaint-service/external-deps/mcp-contract-checklist.md` 命中；`grep -E 'session_id_required|envelope|pii_masked_at_server|mock_aligned|confirmed_by|confirmed_date|gaps' docs/complaint-service/external-deps/mcp-contract-checklist.md` 均命中（可在表头行或同表相邻表头）；`grep -F '{code, message, data}' docs/complaint-service/external-deps/mcp-contract-checklist.md` 命中；14 个 tool 名均 `grep -q` 命中。

## 14.2 rag-service 入库与质量确认（REQ-CJ-005）

- [x] 14.2.1 新建 `docs/complaint-service/external-deps/rag-kb-confirmation.md`：法规入库表（`ingested`/`corpus_count`/`sample_doc_ids`/`owner_team`/`confirmed_date`）+ 范本入库表（`ingested`/`template_count`/`sample_template_ids`/`owner_team`/`confirmed_date`）+ 质量门槛表（`metric`/`threshold`/`measured_value`/`dataset`/`method`/`measured_date`/`owner_team`/`gaps`）。门槛预填 top-3≥85%、召回≥90%、法规覆盖≥95%；实测与 dataset 可 `TBD`。写明阻塞影响与 Mock `rag_kb` 缓解；不改图节点。
  **验证**：`test -f docs/complaint-service/external-deps/rag-kb-confirmation.md`；`grep -E '^\| *ingested *\|' docs/complaint-service/external-deps/rag-kb-confirmation.md` 命中；`grep -E '^\| *metric *\| *threshold *\|' docs/complaint-service/external-deps/rag-kb-confirmation.md` 命中；`grep -E 'corpus_count|sample_doc_ids|template_count|sample_template_ids' docs/complaint-service/external-deps/rag-kb-confirmation.md` 命中；`grep -E 'top-3|召回|覆盖' docs/complaint-service/external-deps/rag-kb-confirmation.md` 命中；`grep -E '85|90|95' docs/complaint-service/external-deps/rag-kb-confirmation.md` 命中；`grep -E 'Mock|rag_kb' docs/complaint-service/external-deps/rag-kb-confirmation.md` 命中。

## 14.3 LLM 配额与连通确认（REQ-CJ-022/023）

- [x] 14.3.1 新建 `docs/complaint-service/external-deps/llm-quota-confirmation.md`：三模型表（DeepSeek `deepseek-r1`、DashScope `qwen-turbo`/`qwen-plus`），表头列 `provider`/`model`/`quota`/`qpm`/`qps`/`unit_price_input`/`unit_price_output`/`unit_price_reasoning`/`endpoint`/`network`/`tls`/`connectivity`/`owner_team`/`confirmed_date`/`gaps`。数值可 `TBD`；千问 `unit_price_reasoning` 填 `N/A`。备注 PRD 8.7 对照（deepseek-r1 5 QPS、qwen-turbo 20 QPS）且标明非合同值。专节 `reasoning_content`：字段名/类型/与 content 分列/reasoning token 计费/`cot_encrypted` 衔接；声明本批不改 env、不打开真调用；批次11 `USE_MOCK_LLM=false` ≠ 已就绪。不改 `llm-layer` / `model_router.yaml`。
  **验证**：`test -f docs/complaint-service/external-deps/llm-quota-confirmation.md`；`grep -E 'deepseek-r1|qwen-turbo|qwen-plus' docs/complaint-service/external-deps/llm-quota-confirmation.md` 三模型均命中；`grep -E '^\| *provider *\| *model *\| *quota *\|' docs/complaint-service/external-deps/llm-quota-confirmation.md` 命中；`grep -E 'qpm|qps|unit_price_input|unit_price_output|unit_price_reasoning|endpoint|network|tls|connectivity' docs/complaint-service/external-deps/llm-quota-confirmation.md` 均命中；`grep reasoning_content docs/complaint-service/external-deps/llm-quota-confirmation.md` 命中；`grep cot_encrypted docs/complaint-service/external-deps/llm-quota-confirmation.md` 命中；`grep -E 'USE_MOCK_LLM|Mock LLM|不改.*env' docs/complaint-service/external-deps/llm-quota-confirmation.md` 命中。

## 14.4 传统催收业务系统联调确认（REQ-CJ-015/038）

- [x] 14.4.1 新建 `docs/complaint-service/external-deps/bizsys-integration-confirmation.md`，含三节：
  1）工单推送：`POST /api/v1/complaints` 字段 `complaint_id`/`user_id`/`case_ids`/`complaint_text`/`submit_time`/`source_channel`；幂等；缓解脚本 `scripts/mock/push_complaint.sh`、`scripts/mock/push_batch_complaints.py`。
  2）回调接收：`session_id`+`version` 幂等、重试 10s/30s/60s、死信 `csmp:callback:dlq`、对账 `draft_generated` 超 1h、`callback_url`/`hmac` 或 `auth` 列（可 TBD/N/A）。
  3）复核界面：`PUT /api/v1/complaints/{id}/review`；六动作 `approve`/`modify_verdict`/`modify_response`/`reject_rejudge`/`add_evidence`/`escalate`；verdict 四值且禁止 `normal`；**10 项必显**须写出：投诉基本信息、案件信息、证据链、判责草稿、合规意见、用户诉求、答辩初稿、整改建议、Agent 链路、操作区；`reject_count` 上限 2。
  对接方/确认人/日期/缺口可 `TBD`。不改 API / `callback-service` / `review-service`。
  **验证**：`test -f docs/complaint-service/external-deps/bizsys-integration-confirmation.md`；`grep -F 'POST /api/v1/complaints' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 命中；`grep -E 'complaint_id|case_ids|complaint_text|source_channel' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 命中；`grep -E 'session_id|csmp:callback:dlq|10s|30s|60s' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 命中；`grep -E 'approve|modify_verdict|modify_response|reject_rejudge|add_evidence|escalate' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 六动作均命中；`grep -E 'agent_violation|user_misunderstanding|compliant_but_poor_experience|pending' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 四值均命中；`grep -E '禁止.*normal|normal' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 命中禁止声明；对十项逐个 `grep -q`：`投诉基本信息`、`案件信息`、`证据链`、`判责草稿`、`合规意见`、`用户诉求`、`答辩初稿`、`整改建议`、`Agent 链路`、`操作区`（「AI 判责草稿」可写作「判责草稿」）；`grep -E 'push_complaint.sh|push_batch_complaints.py' docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 命中。

## 14.5 负向收口（禁止改业务代码）

- [x] 14.5.1 确认本批 apply 交付路径 ⊆ `docs/complaint-service/external-deps/**`：不得改 `services/complaint-service/`、`docker-compose*.yml`、`Dockerfile`、`.github/`、`config/*.env`。六文件均存在。
  **验证**：`ls docs/complaint-service/external-deps/README.md docs/complaint-service/external-deps/mcp-server-tracking.md docs/complaint-service/external-deps/mcp-contract-checklist.md docs/complaint-service/external-deps/rag-kb-confirmation.md docs/complaint-service/external-deps/llm-quota-confirmation.md docs/complaint-service/external-deps/bizsys-integration-confirmation.md` 六个路径均存在；本批相关变更文件列表 ⊆ `docs/complaint-service/external-deps/`（例如对 apply 新增文件：`find docs/complaint-service/external-deps -type f | sort` 恰好上述六文件；`git status --short -- services/complaint-service docker-compose.dev.yml docker-compose.complaint.yml .github` 不得出现本批新引入的修改——既有工作区脏文件可忽略，但本批 commit/变更不得包含这些路径）。

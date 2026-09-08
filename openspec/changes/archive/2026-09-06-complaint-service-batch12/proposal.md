## Why

批次0～11 已把 complaint-service 编码、测试与部署编排收口，但真实 MCP Server、RAG 法规/范本入库、LLM 配额连通、传统催收业务系统推送/回调/复核界面仍由外部团队交付。模块14（任务 14.1～14.4）是项目管理收口：把这些外部依赖的进度、契约与质量门槛落成可跟踪文档，避免编码侧误切真实现、误把 stub 网关当联调完成。

## What Changes

- **14.1 MCP Server 跟踪与契约对齐**：为 4 个 Server（`business_ticket` / `call_qc` / `rag_kb` / `data_platform`）共 14 个 tool 建立进度跟踪表 + 契约对齐清单，对照 PRD DD-05 §4.4 / REQ-CJ-034；编码侧继续用 Mock MCP。
- **14.2 rag-service 入库与质量确认**：确认投诉监管法规、投诉处理范本已入库；记录质量门槛 top-3≥85%、召回≥90%、法规覆盖≥95%（REQ-CJ-005）及测量口径/缺口。
- **14.3 LLM 配额与连通确认**：与算法/基建对齐 DeepSeek `deepseek-r1` 与 DashScope `qwen-turbo`/`qwen-plus` 的配额、QPM、单价、网络连通、`reasoning_content` 格式（REQ-CJ-022/023）；编码侧继续用 Mock LLM。
- **14.4 传统催收业务系统联调确认**：对齐工单推送、回调接收、复核界面（REQ-CJ-015/038）；本地继续用 Mock 推送脚本。
- 交付物全部为文档（跟踪表 + 确认文档），路径钉死在 `docs/complaint-service/external-deps/`。

## Capabilities

### New Capabilities
- `complaint-service/ext-mcp-alignment`: 4 Server × 14 tool 的外部进度跟踪表与契约对齐清单（对照 DD-05 §4.4 / REQ-CJ-034），不实现真实 MCP Server。
- `complaint-service/ext-rag-alignment`: 与 rag-service 的法规/范本入库确认及质量门槛记录（REQ-CJ-005）。
- `complaint-service/ext-llm-quota`: 与算法/基建的 LLM 配额、QPM、单价、网络连通、`reasoning_content` 格式确认（REQ-CJ-022/023）。
- `complaint-service/ext-bizsys-alignment`: 与传统催收业务系统的工单推送、回调接收、复核界面联调确认（REQ-CJ-015/038）。

### Modified Capabilities
（无。本批不改 `mcp-client` / `llm-layer` / `callback-service` / `review-service` / `api-layer` / `deploy-*` 的代码级需求。）

## Impact

- **交付物（仅 apply 时写文档）**：`docs/complaint-service/external-deps/` 下的跟踪表与确认文档（见 design 钉死路径）。不改 `services/complaint-service/` 业务代码、不改 Dockerfile/compose、不改 API、不改图节点、**不改** `config/*.env`。
- **编码侧 / 与批次11 env 关系**：dev/test 继续 Mock；批次11 已写入的 pre/prod `USE_MOCK_*=false` **本批不得改回或「修正」**——文件上 Mock=false ≠ 外部已就绪；真后端与 stub→真网关切换是外部交付后的**后续变更**。本批文档须写明该边界。
- **契约基线**：MCP 入参/出参/异常以 DD-05 §4.4 为准；Server 名以批次3 `mcp-client` 权威名（下划线）为准，PRD 连字符别名仅作对照列。
- **验证**：文档存在性 + Markdown **表头行**完备性（`| col |` 形态 grep）+ 关键字符串。禁止 docker build、pytest 业务验收作为本批完成条件。
- **本批完成标准（对齐 step8「不编码、项目管理」）**：六份文档落档且表头/契约预填齐全（未知可 `TBD`）。**不等于**外部团队全绿、质量实测达标或已切真流量；归档时允许跟踪表仍为 `blocked`/`TBD`。
- **Non-Goals**：禁止改 complaint-service 研判链路/图节点/API；禁止实现真实 MCP Server；禁止把批次11 stub 网关换成真 MCP 实现；禁止 Skill 架构；禁止本批改 `USE_MOCK_*` / compose。

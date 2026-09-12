## Why

qc-service M1 已有离线 HTTP（replay/records/reviews），但没有浏览器页，验收仍靠 curl。投诉已有 `services/complaint-service/web` 静态人审页。质检需要同等演示台：灌数 → 点开检 → 看扣分/条款/时间线 → 有扣分必须人审才完成；同时打开真 LLM，并允许 qc 调 `POST /kb/search`（覆盖 M1「禁止检索」）。

## What Changes

- 新增独立静态前端 `services/qc-service/web/`（无构建、无 React）：灌 3 条待检通话、点行开检、命中表、kb 条款、本地时间线、人审中文按钮
- 演示 API：`POST /api/v1/qc/demo/seed`、`GET /api/v1/qc/demo/pending`；开检走已有 `POST /qc/replay/{session_id}`（禁止主路径 `replay/raw`）
- **BREAKING（相对 M1 打分）**：可计分扣分合计 >0 → `PENDING_REVIEW`；零扣分 → `DONE`（不再仅 agent-high 才待复核）
- **BREAKING（相对 M1 检索禁令）**：qc 实现 `POST /kb/search` 客户端；演示默认 `QC_R14_RAG_ENABLED=true`；条款回填 hits；`data_materials` 入库 rag；词库改用 rag ~50 词 `current.dict`
- hits HTTP 增加 `source` / `word` / 条款字段；CORS 放行 8080；演示 JWT（role=QC）；compose `QC_MOCK_LLM=false`
- `GET .../trace` 本地时间线 + 可选 LangSmith 外链（无 Key 不崩）
- **不做**：投诉 8 Agent、合前端、`POST /qc/check` ingest、WS、LangGraph、call-service、以 LangSmith 为唯一真相源、改 `openspec/specs/00`～`07`

## Capabilities

### New Capabilities

- `qc-service/demo-ui`: 静态页、灌数按钮、待检点开检、列表/详情、人审中文按钮、健康条、CORS 依赖
- `qc-service/demo-seed`: demo/seed、demo/pending、三场景契约、`data_materials` 话术、唯一 ID、演示 JWT
- `qc-service/kb-search`: qc 调 kb/search、`QC_R14_RAG_ENABLED=true`、失败回退关键词、条款回填 hits；覆盖 M1 禁止检索
- `qc-service/demo-trace`: 按 session 本地落盘时间线 + 可选 LangSmith 外链（对标投诉 v3-trace；qc 无 Agent）

### Modified Capabilities

- `qc-service/engine`: 有扣分必须 `PENDING_REVIEW`；零扣分 `DONE`；R14 允许在开关开启时检索
- `qc-service/api-layer`: hits 字段、CORS、人审误报走 `CORRECT`+手填分
- `qc-service/data-layer`: rag 客户端在演示默认下可调 `kb/search`；词库指向 ~50 词 dict

## Impact

- 代码：`services/qc-service/web/`、`app/routers` 演示/trace、`scoring_service`、`rag_client` 增加 search、compose/env、演示种子 SQL/话术
- HTTP：`/api/v1/qc/demo/*`、`GET /qc/records/{session_id}/trace`；信封仍 `code==="0"`；HTTP `compliance_score`/`risk`
- 依赖：data-platform qc-source；rag `current.dict` + `kb/search` + 入库 `data_materials`；真 DashScope LLM；Redis DB 3
- 权威：本 change delta 覆盖 M1 检索禁令与打分门槛；其余信封/FK/`call_id` 规则仍以 `openspec/specs/qc-service/*` 与 DD-02 为准
- 负责人：Ellis Guo

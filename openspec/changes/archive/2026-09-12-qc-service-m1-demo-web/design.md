## Context

见 `proposal.md` 的 Why。qc-service M1 已归档：离线管道、replay、records/reviews、词库只拉 `current.dict`、`QC_R14_RAG_ENABLED=false`、打分仅 agent-high 进 `PENDING_REVIEW`。投诉演示页在 `services/complaint-service/web/`。本设计只定实现切法，行为以 delta specs 为准。

## Goals / Non-Goals

**Goals:**

- 静态页 + 演示 API 与现有 replay/reviews 接成浏览器主路径
- 打分门槛与 kb/search 按本 change specs 落地
- 时间线本地可读，LangSmith 可选

**Non-Goals:**

- 不设计 LangGraph / 投诉 Agent
- 不实现 ingest/WS/call-service
- 不把演示 seed 做成生产案件写入通道（仅 demo 路由）

## Decisions

### D1 — 前端形态抄投诉静态页

- **选择**：`services/qc-service/web/` vanilla HTML/JS，`python -m http.server 8080`，`API_BASE=http://localhost:8003`。
- **备选**：React SPA。否决：投诉已验证静态页足够演示，且本 change 禁止合前端。

### D2 — 待检登记与 seed 写主数据

- **选择**：`POST /qc/demo/seed` 写 `cases` + `call_records` + `dialog_turns`（speaker=`ai|user`）；Redis `qc:demo:sessions`（或等价）登记本次待检 session_id；`GET /qc/demo/pending` 读登记且无有效 `qc_records`。待检不插 `qc_records`。
- **备选**：复用 DP `pending-qc`（按当天日期）。否决：会混入历史 completed 通话，列表不干净。
- **备选**：前端直连 PG。否决：specs 禁止。

### D3 — 开检只走 replay/{session_id}

- **选择**：详情点待检 → `POST /api/v1/qc/replay/{session_id}`，内部 GET DP qc-source。
- **备选**：`replay/raw`。否决：时间线没有取源步骤。

### D4 — 打分：有扣分即待复核

- **选择**：改 `ScoringService`：`score_eligible` 扣分合计 >0 → `pending_review` / `PENDING_REVIEW`；=0 → `compliant` / `DONE`。`risk_level` / `grade` 公式不变。
- **备选**：仅 UI 把 DONE 显示成待复核。否决：`GET /reviews/pending` 只认库状态。

### D5 — 误报 = CORRECT + 手填分

- **选择**：前端不暴露 `DISMISS`；误报与改分都 POST `CORRECT` + `revised_score`。演示 seed 返回 QC JWT，种子 `users` 行满足 FK。
- **备选**：改 `DISMISS` 自动改回 100。否决：破坏 sample_type=negative 语义；用户已选手填 0–100。

### D6 — 检索与词库

- **选择**：`RagClient` 增加 search；compose 演示 `QC_R14_RAG_ENABLED=true`、`QC_MOCK_LLM=false`；`QC_RAG_SENSITIVE_BASE` 指向 rag-service（约 50 词 dict），不再默认 7 词 mock。apply 提供把 `data_materials` 手册/公约/细则入库的脚本或文档步骤。检索失败回退关键词。
- **备选**：对照表写死条款。否决：用户锁定当场检索。

### D7 — hits 持久化 source/word/条款

- **选择**：Alembic 在 `qc_violations` 增加可空 `source`、`word`、`kb_snippet`（名称以实现为准，HTTP 键 `source`/`word`/条款）。GET records 与 replay `hits` 序列化这些字段。
- **备选**：塞回过渡 JSONB `qc_records.violations`。否决：M1 已停读该列。

### D8 — 时间线 Redis 落盘

- **选择**：管道各步 append Redis `qc:demo:trace:{session_id}`（DB 3，TTL 24h）。`GET /api/v1/qc/demo/{session_id}/trace` 统一给待检与已出分（钉死此路径，不用 records 子路径，避免无行 404）。LangSmith：LLMReviewer 可选 RunTree；`langsmith_url` 仅当 Key+run id+模板齐全。
- **备选**：LangSmith 唯一真相。否决：投诉已否；无 Key 必须可演示。

### D9 — CORS

- **选择**：与投诉相同，放行 8080 / 127.0.0.1:8080，可选 5500。

## Risks / Trade-offs

- [seed 写 cases/call_records] → 仅 `/qc/demo/*`；文档标明演示破例，生产关闭该路由或用环境开关。
- [真 LLM 超时] → 保持 A2011 + AC/规则分；时间线记 LLM degraded。
- [rag 全栈未起] → 演示 compose 拉起 rag-service；入库失败则条款为空但仍出分（specs 允许回退）。
- [打分 BREAKING] → 旧「中低危 DONE」断言必须改测，列入 tasks。
- [DISMISS 仍存在] → API 保留；前端不暴露，避免误报不改分。

## Migration Plan

1. Alembic 加 `qc_violations` 三可空列；种子演示 QC 用户。
2. compose：qc CORS、`QC_MOCK_LLM=false`、`QC_R14_RAG_ENABLED=true`、词库指向 rag-service；rag 入库 `data_materials`。
3. 回滚：关 demo 路由与 CORS 不影响 M1 replay；打分门槛回滚需再发一版（行为 BREAKING）。

## Open Questions

无。trace 路径钉死为 `GET /api/v1/qc/demo/{session_id}/trace`。

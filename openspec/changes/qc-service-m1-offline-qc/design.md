## Context

见 `proposal.md` 的 Why。`services/qc-service` 当前为 FastAPI 占位：服务根 `GET /health`，另有 `app/api/health.py` 挂在 `/api/v1`。PostgreSQL 已有 `0001_init_tables.py` 遗留的 `qc_records` / `qc_rules`（行式规则、无 UNIQUE session_id、无 `grade`/`qc_status`、JSONB `violations`）。尚无 `0011` 迁移。M0 `common/`（错误码、trace、llm 传输、mask、sensitive_words/guardrails 钩子）可用。

宪法已在 `openspec/specs/00_biz_redline.md`～`07_test_constraint.md` 与 `openspec/specs/platform/*`。本设计**只引用**这些文件，**不重写**。

运行时身份（冻结）：服务名 **`qc-service`**，HTTP 端口 **8003**，Redis **DB 3**。

## Goals / Non-Goals

**目标：**

- 8003 上一个进程，用 DP + rag 词库 + PG/Redis 实现 M1 离线全文质检
- 一个 OpenSpec change；之后按 16 轮（0.1–3.4）分层 apply，不再拆 change
- Alembic 0011 把遗留质检表演进为 step4/01 五表形态，不删 `session_id` FK
- 字段映射：库 `compliance_score`/`risk_level`/`qc_result` vs HTTP/事件 `score`/`risk`/`grade`

**非目标：**

- 在 `openspec/specs/00`～`07` 或 `platform/*` 另写宪法
- `POST /qc/check` ingest、`WS /qc/check/stream`、`qc.escalate.human`、`qc.llm.degraded` 频道
- 实现 data-platform ETL/宽表或 call-service
- R14 `POST /kb/search` 与红线 6 `guardrail.pass` 读取器（QC-13.2 / QC-13.3，零代码）
- 修改 `complaint-service` / `rag-service` 主 spec 或其运行时

## Decisions

### D1 — 引用宪法，不复制

- **选择**：分层、MCP vs HTTP vs 事件、LLM 传输、错误码、红线遵循 `openspec/specs/01_arch_constraint.md`、`platform/integration-boundaries`、`04_llm_guardrail.md`、`06_exception_alarm.md`、`00_biz_redline.md`。qc M1 将红线 1–5/7 映射到 R11–R15/R04（DD-02 §15.6）；红线 6 仍属 call-service（M2+）。
- **备选**：在本 change 复制一份「质检宪法」。否决：用户已冻结，且 00～07 已存在。

### D2 — 包结构与 health

- **选择**：按 DD-02 §3 建 `app/routers|services|repositories|events|integrations`。保留现有根 `GET /health`；扩展 payload 为 `ac_version` / `llm_degraded` / `rag_connected`。不以 `/api/v1/health` 为权威。无平行业务 `app/api/` 树，无 WS 模块。
- **备选**：像占位一样把全部路由放 `app/api/`。否决：DD-02 §3 点名 `routers/`。

### D3 — ORM 位置与 Alembic 0011

- **选择**：ORM 放在 `services/qc-service`（如 `app/models/`，或若仓库日后集中则 `common/db/models/qc_*.py`；M1 默认服务内模型，继承 `common.db.base`）。库变更用仓库根 Alembic `0011_qc_service_v2_split_violations.py`：给 `qc_records.session_id` 加 UNIQUE，加 `grade`/`qc_status`/计数，加 `qc_violations`/`qc_alerts`/`qc_reviews`，把 `qc_rules` 重建为版本化 JSONB 包，回填 JSONB `violations` 后停止读取。同一步 MUST NOT DROP JSONB。MUST NOT 删除 `REFERENCES call_records(session_id)`。
- **备选**：推倒重建质检表。否决：0001 已上线；step4/01 禁止删 FK。

### D4 — Redis 任务态 vs 库状态

- **选择**：Redis `qc:job:{session_id}` 持 WAIT_TEXT/RUNNING/DONE（终态另带 `qc_record_id`、`skipped_rules[]`，TTL 24h），DB 3。PostgreSQL `qc_status` 仅 DONE|PENDING_REVIEW|REVIEWED|PARTIAL。WAIT_TEXT 永不 INSERT `qc_records`。`skipped_rules` 耐久 = Redis + `audit_logs.action=qc.skip.recorded`，不加列。
- **备选**：把 WAIT_TEXT 做成库枚举。否决：UNIQUE/score NOT NULL 占坑；DD-02 §3.1。

### D5 — 检测管道单一编排器

- **选择**：`OfflineQcService.run` 是 hits 的唯一写入者，顺序见 spec（AC → 窗口 → 不含 R10 的规则 → LLM → MergeHits → R10 → 打分 → 告警/事件）。HTTP replay/raw/batch 与事件订阅调用同一编排器。轮次隔离：引擎轮次不改 HTTP Schema 或事件 payload。
- **备选**：各 API 路径各自打分。否决：分数不可复现（openspec/07）。

### D6 — score/risk/grade 映射层

- **选择**：保留 DDL 列名。薄序列化层：`compliance_score→score`（仅事件；HTTP records 仍用 `compliance_score` 加 `risk`），`risk_level→risk`。`grade` 在库/HTTP/事件用同一词。`risk_level` 来自 agent+high / 扣分≥20 / 否则 low；**不是**分数分档。`grade` 只来自分数阈值。
- **备选**：把列名改成 JSON 键。否决：step4/01 已冻结。

### D7 — LLM 熔断频道

- **选择**：Redis 键 `qc:llm_degraded` + `system.alert.raised`（`event=qc:llm_degraded`，`error_code=A2011`）。无 `qc.llm.degraded` Pub/Sub 频道。HTTP 200 + `code=A2011` 仍返回 AC+规则分。调用走 `common.llm.transport`。
- **备选**：独立 qc LLM 频道。否决：step6/01 / DD-02 V1.5。

### D8 — DP 与 rag 客户端

- **选择**：qc 作为 HTTP 客户端访问 `QC_DP_BASE` + step5/02 §3.4 路径（`/api/v1/data/sessions/{session_id}/qc-source`、`/api/v1/data/sessions:pending-qc`）以及 rag `GET /api/v1/sensitive-words/current.dict`。M1 可消费这些只读 API；本 change 不实现 DP。契约测试/夹具放在 qc（`tests/fixtures/qc_source.json`）。`QC_R14_RAG_ENABLED=false`；rag 客户端无 search 方法。
- **备选**：qc 内嵌对 `cases` 的 JOIN SQL。否决：缺元数据则 skip；qc 不得反查 cases。

### D9 — Apply 拓扑（不再开 change）

- **选择**：同一 change，16 轮 apply 对齐任务清单 0.1–3.4；一轮一层。QC-11.2 仍是一个 ID（3.2 = 断言 1–14，3.3 = 15–28）。QC-13.2/13.3 只登记，不占轮次。
- **备选**：16 个 OpenSpec change。否决：用户已冻结。

### D10 — MCP

- **选择**：qc 只实现 `call_qc.query_qc_violations` / `query_qc_alerts`。ASR/会话工具不在此。投诉节点不直连 qc HTTP。
- **备选**：qc 实现全部 `call_qc` 工具。否决：DD-02 §9.2 / 01_arch_constraint 的 MCP 拆分。

## Risks / Trade-offs

- [0011 vs 线上 0001 qc_rules 行式 schema] → 谨慎重建/迁移；切换后种子 v1.0。缓解：迁移加版本化表形态；测试先种子 `call_records`。
- [本 change 不实现 DP qc-source] → replay/{session_id} 与 etl 路径需要运行中的 DP 或 Mock。缓解：QC-13.1 夹具 + 测试 Mock 客户端；冒烟文档写明 DP 依赖。
- [M1 可缺 call.session.ended] → 须依赖 etl.task.done 或手动 replay。缓解：spec 允许缺 ended。
- [LLM 豁免 vs 平台 0.7 门控] → `qc_review_exemption` 已在 DD-02 / step5/03；不得推广到其他服务。
- [HTTP 200 + A2003] → 只看 HTTP 状态的客户端会误开工。缓解：scaffold spec 与 API 文档钉死信封 `code==="0"`。
- [一轮 apply 同时做引擎+HTTP+事件会撑爆 token] → 16 轮，一轮一层。

## Migration Plan

1. 在 M0 共用 PG 上 apply Alembic 0011；任何 qc INSERT 前先有 `call_records` 种子。
2. 种子 `qc_rules` v1.0（R14 十八词）为唯一 active 包。
3. 部署 `qc-service:8003`，Redis DB 3；HEALTHCHECK `/health`。
4. 回滚：若生产 `qc_records` 尚未依赖新 CHECK，可 revert 0011；否则停进程、保留表（不 DROP FK）。

## Open Questions

无影响 spec、方案或 16 轮任务拆分的未决问题。

已知文档缺口（本 change **不发明新口径**；以 DD-02 V1.17 为准）：

- GET `/qc/alerts` 空列表、GET `/qc/reviews/pending` 空列表，DD-02 未像 records 列表/stats 那样写成「200 + items=[]」。ack/缺复核码（A2006/A2007）仍按已写口径。不发明新的空列表错误码。
- step5/02 通用「失败时 data=null」vs DD-02 A2011 仍返回 AC+规则 `data`：跟 DD-02。
- 断言 1（`score≤60` / `grade=SERIOUS`）vs 单条 agent-high（−20 → 80/PASS/high）：夹具不同；acceptance spec 要求断言 1 用足够多的 high hits。
- DP ETL 实现按冻结仍不在本 change。

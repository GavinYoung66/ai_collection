## Why

M1 需要在 `data-platform + rag-service + qc-service` 三件套上跑通离线全量质检：通话结束且双向全文齐后，AC + R01–R15（含 skip）+ LLM（可熔断）→ MergeHits → 打分 → 事后主管告警与事件回流。仓库已有 `openspec/specs/00`～`07` 与 `platform/*` 宪法，本 change 只落地 `qc-service` 行为，不另起全局架构。

## What Changes

- 新增独立服务 `qc-service`（端口 **8003**、Redis **DB 3**），包结构、`QC_*` 配置、A2 HTTP 信封（A2001–A2011）
- 落地 5 表 ORM/迁移、Redis `qc:job`、规则种子 v1.0、rag 词库客户端、只读消费 DP `qc-source` / `pending-qc`
- 实现离线检测链：AC 窗口、R01–R15 skip、MergeHits、R10 后置、打分编排、`qc_review_v1` 与 LLM 熔断 A2011
- 暴露离线 HTTP：replay / raw / batch / records / alerts / reviews / rules；发布 6 频道、订阅 4 频道；MCP `call_qc` 仅两查询
- 可观测：`GET /health` 三字段、脱敏、埋点；断言 1–28 + 冒烟；M2+ RAG 检索与红线 6 **不编码**
- **不做（非 BREAKING，本服务为新建）**：`POST /qc/check` ingest、`WS /qc/check/stream`、`qc.escalate.human`、`qc.llm.degraded` 频道、R14 `POST /kb/search`、DP ETL/宽表、call-service、投诉研判全文 FR
- **不修改**已有 `complaint-service` / `rag-service` 主 spec

工作方式冻结：一个服务 = 一个 change；本 propose 一次写出 9 个 New Capabilities；之后由人工下令，同一 change 按任务清单 0.1～3.4 分 16 轮 apply；全部通过后再 archive 一次。禁止为轮次/模块再 `new change`。

## Capabilities

### New Capabilities

- `qc-service/scaffold`: 包结构、`QC_*` 配置、deps、A2 HTTP 信封（QC-1.1～1.4）
- `qc-service/data-layer`: 5 表、Redis `qc:job`、规则种子、rag 词库客户端、DP qc-source/pending-qc、qc-source 夹具（QC-2.*、QC-3.*、QC-13.1）
- `qc-service/engine`: AC、R01–R15 skip、MergeHits、打分编排（QC-4.1～4.8）
- `qc-service/llm-review`: `qc_review_v1`、熔断 A2011、`system.alert.raised`（QC-5.1～5.3）
- `qc-service/api-layer`: replay/raw/batch/records/alerts/reviews/rules（QC-6.*、QC-8.*）
- `qc-service/events`: 发布 6 频道、订阅 4 频道（QC-7.1～7.3）
- `qc-service/mcp-call-qc`: 仅 `query_qc_violations` / `query_qc_alerts`（QC-9.1）
- `qc-service/observability`: `GET /health`、脱敏、埋点（QC-10.1～10.3）
- `qc-service/acceptance`: DD-02 §8.2 断言 1–28、冒烟、镜像/env、M2+ 不编码（QC-11.*、QC-12.*、QC-13.2、QC-13.3）

### Modified Capabilities

- （无）不覆盖、不修改已有 `complaint-service` / `rag-service` 主 spec；不复制 `openspec/specs/00`～`07` 与 `platform/*` 成新宪法。

## Impact

- 代码：`services/qc-service/`（现有 `/health` 占位可保留）、`common/db/models/qc_*.py` 或服务内 models（与仓库惯例一致）、Alembic `*qc*`、`common/llm/prompts/qc_review.yml`
- HTTP：`http://qc-service:8003/api/v1/qc/*`；`GET /health` 挂服务根。HTTP 键 `compliance_score`/`risk`/`grade`/`qc_result`；事件 `qc.check.completed` 键 `score`/`risk`/`grade`
- 事件：发布 `qc.alert.raised` / `qc.check.completed` / `qc.complaint.suspected` / `qc.stop_call.detected` / `qc.review.done` / `system.alert.raised`；订阅 `call.session.ended` / `etl.task.done` / `kb.sensitive_updated` / `strategy.updated`
- 依赖：M0 `common/`；只读消费 data-platform qc-source/pending-qc；只拉 rag `current.dict`；**不起** call-service
- 权威：DD-02 V1.17；任务清单 v1.3（拆分与 16 轮）；DDL step4/01；错误码 step6/01；信封与 DP 路径 step5/02。口径冲突以 DD-02 V1.17 为准
- 负责人：Ellis Guo
- 不做：实时 ingest、通话页 WS、投诉研判、call/case/review 全文 FR、DP ETL 实现

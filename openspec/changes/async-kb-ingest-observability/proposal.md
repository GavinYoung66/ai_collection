## Why

V5 与 `optimize-rag-v5-ingest-runtime` 已修好资产批量写入与超时文案，但 `POST /kb/documents` 仍同步等完全程返回 `active`：大 docx 易假超时、列表看不到 processing、无进度、可重复并行上传、reindex 静默丢图/表资产、失败可卡 processing、DB Session 长占连接池。调试台与调用方需要异步入库 + 可观测 + 防重 + 预览收口，且明确 **BREAKING** 旧同步契约。

## What Changes

- **A1–A4 异步契约（BREAKING）**：校验通过后尽快返回 `doc_id` + `status=processing`（HTTP 200 + `status=processing`）；解析/正文/资产索引后台继续；完成后 `active` 或 `failed`。后台失败 MUST 置 `failed` 且列表/详情可定位（含 `ingest_error`/`error_code`）。Gradio 收到 processing 立刻展示并引导刷列表。同步等完全程返回 `active` 作废。
- **A5 进度可观测**：processing 期间暴露 `ingest_stage`（parse|body_index|asset_index|done）与可选 `ingest_progress`（按 stage 映射，见 design）；列表或详情可取；完成后清空或标 done。
- **A6 图片资产预览**：`GET /kb/documents/{id}` 响应增加 `assets` 元数据数组；Gradio 上传页/列表侧按 `storage_path` 读共享 `KB_ASSETS_DIR` 预览 image（不新建公网静态下载 API）。
- **A7 重复上传防护**：Gradio 阻止或强提示；**后端 MUST** 拒绝「同 title 仍 processing」的重复 POST。
- **B1–B3 可见性与状态机**：早返回前列表 MUST 可见 processing；禁止长期卡 processing（含进程重启后 startup sweep）；后台短事务；**processing 期间 MUST NOT 可检索**。
- **B4 后台总时限**：`KB_INGEST_JOB_TIMEOUT` 超时 → failed。
- **B5 reindex 资产语义**：**默认仅正文 reindex** + 强警告 + `assets_rebuilt=false`；完整重建资产为可选升级。
- **B6 reindex 超时**：reindex API 保持同步；Gradio 用与 upload 同级 Timeout + 轮询 doc status，禁止 90s 假报后端不可用。
- **B7 降级/失败醒目**：`failed`/`degraded` 在上传结果与列表醒目展示。
- **C1 索引孤儿补偿**：Milvus/ES 已写而 PG 失败时 MUST 补偿 `delete_by_doc`。
- **C2 错误脱敏**：对外不抛裸异常栈；详情进日志 + trace_id。
- **C3 Gradio _inflight**：按操作+文件粒度；上传中仍可刷列表。
- **P2 补强**：后台临时文件 success/failed 后清理；`ingest_progress` 按 stage 映射（可空）；README/API 文档 BREAKING 说明。
- **Non-Goals**：不改 JWT；不改一行一 chunk；不新建公网静态文件下载 API；不做 embed 超大批次拆片、`wait_for` 杀 to_thread、PDF 双开合并、流式上传防 OOM、reindex 异步化；不起强制 Celery；禁止改 archive；ConnectError/ReadTimeout 禁止回退。

## Capabilities

### New Capabilities

无。（增量演进既有能力，不新增 capability 目录。）

### Modified Capabilities

- `rag-service/document-ingest`: 异步 processing、进度/失败原因字段、processing 不可检索、僵尸回收、临时文件清理、详情 assets 元数据、后端防重、短事务、job 超时、孤儿补偿、reindex 默认仅正文、错误脱敏；**BREAKING**。
- `rag-service/multimodal-ingest`: 资产阶段与后台 job 协作；后端防重 MUST；预览元数据经详情 assets 暴露。
- `rag-service/gradio-frontend`: processing 展示、stage 列、防重、详情 assets 预览 image、reindex 长超时+轮询、失败醒目、_inflight 放宽。

## Impact

- **API（BREAKING）**：`POST /kb/documents` 早返回 processing；`GET /kb/documents/{id}` 增 `assets`、`ingest_stage`、`ingest_progress`、`ingest_error`；列表增进度/失败字段；重复 POST 拒绝；processing 文档不可被检索命中。
- **代码**：`documents.py`、`ingest_service.py`、`search_service.py`（过滤 processing）、`doc_repo`/ORM/Alembic、`gradio/`、startup sweep、`README.md`。
- **配置**：`KB_INGEST_JOB_TIMEOUT`（默认 1800s）。
- **兼容性**：禁止修改 archive；主 spec archive 时 sync。

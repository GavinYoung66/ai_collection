## Context

See proposal.md。当前同步 ingest、early commit 半可搜、无进度 API、reindex 丢资产、Gradio 90s reindex 假超时。约束：不改 JWT、不改一行一 chunk、不新建公网静态下载 API、保留 ConnectError/ReadTimeout 文案。

## Goals / Non-Goals

**Goals:**
- 200 + processing 早返回；后台 active/failed；失败可经 `ingest_error` 定位。
- 进度 stage + 可选 progress；processing 不可检索；僵尸 processing 回收。
- 详情 `assets` 元数据供 Gradio 预览；前后端防重 MUST。
- reindex 默认仅正文 + 强警告；Gradio reindex 长超时+轮询。
- 临时文件清理、孤儿补偿、错误脱敏。

**Non-Goals:**
- Celery、HTTP 202、reindex 异步化、embed 拆批、OCR 并发、流式上传、公网静态下载 API。

## Decisions

### 决策 1：早返回 = HTTP 200 + status=processing
- **方案**：`success_response({doc_id, status:"processing"})`；校验失败仍同步 BizException。
- **替代**：202。不选。

### 决策 2：后台 = asyncio.create_task + 阶段短事务
- **方案**：持久化文件 → 创建 processing 行 + commit → 返回 → 后台 parse/body/asset；每阶段独立 Session；更新 stage/progress/error；成功 active，失败 failed；**finally 删除临时文件**。
- **替代**：Celery。不选。

### 决策 3：进度与失败字段落 PG
- **方案**：Alembic 增加可空列：`ingest_stage`、`ingest_progress`（int 0–100，可空）、`ingest_error`（varchar/text，可空，存业务可读短文案或 error_code）。列表+详情返回；完成 active/failed 后 stage=done 或清空，error 在 failed 时保留至下次 reindex/重传覆盖。
- **progress 映射（本版）**：仅按 stage 离散映射，不做行级 OCR 细粒度——`parse=25`、`body_index=50`、`asset_index=75`（无资产时跳过 asset_index 直接 done=100）；`ingest_progress` 可空，有则按上表写入。
- **替代**：Redis 进度。不选为主。

### 决策 4：job 总时限与僵尸回收
- **方案**：`KB_INGEST_JOB_TIMEOUT` 默认 1800s；超时 → failed + `ingest_error`。`main.py` startup（或等价）扫描 `status=processing` 且 `update_time` 超过 job 时限的文档 → 置 failed + `ingest_error="ingest job timeout or worker lost"`。
- **理由**：覆盖进程重启丢 task（B2）。

### 决策 5：防重 — 前后端 MUST
- **方案**：Gradio 上传前查列表 processing+同名阻止/强提示。后端 `POST /kb/documents`：若存在 `title=filename` 且 `status=processing` 的未删文档 → 返回明确业务错误（如 B0001「同名文件正在入库中」），不得再开并行任务。文件 hash 检测为可选增强，非本版 MUST。
- **替代**：仅 Gradio。不足。

### 决策 6：预览元数据 = 详情响应 assets 数组（方案 A）
- **方案**：`GET /kb/documents/{id}` 响应增加 `assets: [{asset_type, page, storage_path, mime_type}]`（image 预览足够；table 可带 storage_path 供 content_json）。Gradio 读共享盘渲染，**不**新增 `/kb/assets/{id}/file` 静态端点。
- **替代**：独立 `GET .../assets`。不选，详情一次拉齐更简单。

### 决策 7：reindex 默认仅正文（B5）
- **方案**：**默认路径** = 清 chunks/向量/清 assets，仅按 `doc.content` 重切；响应 `assets_rebuilt=false` + API/Gradio 强警告。完整「从原文件重抽资产」为 **可选升级**，非本变更默认。
- **理由**：无原文件持久化时无法默认完整重建。

### 决策 8：reindex 保持同步 + Gradio 长超时轮询（B6）
- **方案**：`POST .../reindex` **不**改为异步；Gradio 使用与 upload 同级 `_UPLOAD_TIMEOUT`（600s），超时后按 ReadTimeout 语义提示处理中并轮询列表/详情 status，禁止 90s 误报「后端不可用」。
- **替代**：reindex 也早返回 processing。本版 scope 外（Non-Goal）。

### 决策 9：processing 不可检索
- **方案**：混合检索/recall 路径 MUST 过滤 `kb_documents.status != active`（或等价），processing/failed 文档 chunks 不得命中。
- **理由**：避免半成品正文被搜到。

### 决策 10：索引孤儿补偿（C1）
- **方案**：Milvus/ES 写入后 PG/后续失败 → `delete_by_doc` + failed。

### 决策 11：错误脱敏（C2）与 Gradio 锁（C3）
- **方案**：API message 固定模板；`str(e)` 仅日志。`_inflight` 按 `(op, key)`；上传中允许 `refresh_docs`。

## Risks / Trade-offs

- **[进程重启丢 task]** → 决策 4 startup sweep 兜底。
- **[同进程 task 背压]** → 可选限制并发入库数；超额 B0007「入库繁忙」。
- **[reindex 与旧「重建资产」scenario 张力]** → 决策 7 明确默认仅正文。
- **[BREAKING]** → README + 调用方轮询约定（task 7.3）。

## Migration Plan

- 部署：Alembic（stage/progress/error）；重建 rag-service + gradio；配置 job timeout。
- 回滚：恢复同步 ingest；新列可空不影响旧代码。
- 调用方：接受 processing，轮询至 active/failed，读 `ingest_error`。

## Open Questions

无。

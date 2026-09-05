# Tasks

实现清单，按依赖排序。绑定 delta specs 与 design 决策。禁止改 archive；禁止回退 ConnectError/ReadTimeout 文案；不新建公网静态下载 API。

## 1. 进度、失败原因字段与配置

绑定：document-ingest「入库进度可观测」「文档状态生命周期」；design 决策 3/4。

- [x] 1.1 Alembic 为 `kb_documents` 增加可空 `ingest_stage`、`ingest_progress`（int 0–100）、`ingest_error`；ORM/`doc_repo` 列表与详情返回。验证：迁移 upgrade；列表/详情 JSON 含新字段。
- [x] 1.2 增加 `KB_INGEST_JOB_TIMEOUT`（默认 1800s）并在 settings/README/.env.example 登记。验证：配置可读。
- [x] 1.3 后台按 stage 写入离散 progress（parse=25、body_index=50、asset_index=75、done=100；无资产跳过 asset_index）。验证：单测或日志断言 stage 切换时 progress 符合映射。

## 2. 异步上传 API（BREAKING）

绑定：document-ingest「文档上传与自动入库」「重复上传后端拒绝」「后台入库临时文件清理」；design 决策 1/2/5；覆盖 A1–A4、A7、B1。

- [x] 2.1 `POST /kb/documents`：校验通过 → 持久化文件 → 创建 processing → **commit** → 返回 200 + doc_id + status=processing → 启动后台任务。验证：响应 processing 且列表立刻可见。
- [x] 2.2 后台任务更新 stage/progress；成功 active，失败 failed + `ingest_error`；校验失败不建任务。验证：两路径 status/error 正确。
- [x] 2.3 后端防重：同 title+processing 重复 POST → 明确业务错误（B0001），不启第二条任务。验证：单测/API 测。
- [x] 2.4 后台 finally 删除本次临时上传文件（active/failed 均清理）。验证：任务结束后 temp 路径不存在。

## 3. 短事务、超时、僵尸回收、不可检索、孤儿补偿、脱敏

绑定：document-ingest 相关 ADDED；design 决策 4/9/10/11；覆盖 B2–B4、C1–C2。

- [x] 3.1 后台各阶段短 Session/短事务。验证：阶段边界单测或审查通过。
- [x] 3.2 超过 `KB_INGEST_JOB_TIMEOUT` → failed + `ingest_error`。验证：缩短 timeout 注入测。
- [x] 3.3 startup sweep：超时 processing → failed + `ingest_error`。验证：启动后僵尸 doc 被回收。
- [x] 3.4 检索路径过滤非 active 文档（processing/failed 不可命中）。验证：单测或检索测 processing doc 不返回。
- [x] 3.5 Milvus/ES 已写后 PG 失败 → 补偿 `delete_by_doc`。验证：失败路径清理被调用。
- [x] 3.6 API message 脱敏，裸异常仅日志+trace_id。验证：单测无栈痕迹。

## 4. 详情 assets 元数据与资产阶段

绑定：document-ingest「文档详情暴露资产元数据」；multimodal「资产阶段与异步入库进度协作」。

- [x] 4.1 `GET /kb/documents/{id}` 响应增加 `assets` 数组（asset_type/page/storage_path/mime_type）。验证：含图 doc 详情含 image assets。
- [x] 4.2 资产处理在后台推进 asset_index；OCR skip 不削弱批量 A3003。验证：含图 doc stage 与容错符合 spec。

## 5. reindex 语义（默认仅正文）

绑定：document-ingest「重新解析入库」；design 决策 7/8；覆盖 B5–B6。

- [x] 5.1 reindex 默认仅正文：响应 `assets_rebuilt=false`；API/注释说明不恢复多模态。验证：含资产 doc reindex 后行为与字段符合 spec。
- [x] 5.2 reindex **保持同步**；Gradio 用 upload 同级 Timeout + 读超时后轮询 status，禁止 90s 假报宕机；确认前强警告。验证：Gradio reindex 路径审查+文案测。

## 6. Gradio 异步 UX、防重、预览、锁

绑定：gradio-frontend 全部 MODIFIED/ADDED；覆盖 A3、A5–A7、B7、C3。

- [x] 6.1 上传返回 processing 立刻展示；列表含 ingest_stage（及 failed 时 ingest_error）；failed/degraded 醒目。验证：文案/字段；ConnectError/ReadTimeout 不回归。
- [x] 6.2 Gradio 防重：processing 同名阻止/强提示（配合 2.3）。验证：同名再传被拦。
- [x] 6.3 选中 doc 调详情取 `assets`，读 `KB_ASSETS_DIR` 预览 image。验证：有图 doc 可见预览。
- [x] 6.4 `_inflight` 按操作+文件粒度；上传中 refresh_docs 可用。验证：上传中可刷列表。

## 7. 文档与回归总检

- [x] 7.1 相关单测通过（async、防重、sweep、不可检索、assets、orphan、脱敏）。验证：`pytest` 目标模块绿。
- [x] 7.2 冒烟：大 docx 上传 → processing → stage 推进 → active/failed+error；防重；预览 image；reindex 警告。验证：按 proposal 勾选。
- [x] 7.3 更新 rag-service README/API 说明 **BREAKING**：POST 早返回 processing、轮询约定、新字段、防重、processing 不可检索。验证：文档已更新且与 spec 一致。

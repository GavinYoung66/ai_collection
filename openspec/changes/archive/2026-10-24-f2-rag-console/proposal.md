# Proposal

## Why

F0 管理台底座、F1 智能投诉、F2 合规质检均已归档（qc-console 19/19 收口）；`/kb/documents` 仍为 F0 占位页（ModulePlaceholder）。旧演示 `services/rag-service/gradio/`（app.py 997 行 + formatters.py 603 行）承载知识库调试能力，但为内网 Gradio 单体、无鉴权角色体系、无工程化保障，与 SSOT《frontend/前端架构.md》§5.0 D 表 9 个已上线端点脱节。本批（F2 RAG 线）将 RAG 知识库功能平移进 Vue 3 管理台，使 admin/ai_trainer 获得与 §5.4 功能树、§11.1 路由表逐条对齐的正式生产界面。

## What Changes

- 新建 `frontend/src/modules/rag/` 完整业务模块（`index.ts` / `routes.ts` / `api.ts` / `types.ts` / `views/` / `components/` / `composables/` / `__tests__/`），替换 F0 占位路由，满足 BizModule 自包含契约；`app/router/index.ts` 装配行零改动（ragModule 已挂载，router/index.ts:7,24）。
- 路由按 §11.1 落 3 条：`/kb/documents`（KbDocuments）、`/kb/search`（KbSearch）、`/kb/sensitive-words`（KbSensitive）；`meta.roles` 均 `['admin','ai_trainer']`，keepAlive 均 true；视图一律 `() => import()` 懒加载；`defineOptions` name 与路由 name 逐字一致；同步更新 `app/__tests__/route-matrix.spec.ts`。
- 接入 §5.0 D 表全部 9 端点（一律 `/api/v1/kb/*`、`/api/v1/sensitive-words/*` 全前缀）：documents 列表/上传/详情/删除/重建索引/摘要编辑、kb/search 混合检索（**answer 固定 false 不下发**）、sensitive-words current.dict/refresh。全部使用 `TIMEOUT.normal`（12s），不新增超时常量、不改网关模板、不改后端。
- 上传为异步入库（rag-service README §G BREAKING / documents.py:66）：前端上传成功即 `processing`，按 stage→progress 映射（parse=25/body_index=50/asset_index=75/done=100）展示进度，轮询列表直至 active/failed（终态自停、unmount 清理、失败熔断）；轮询参照 `modules/complaint/composables/usePolling.ts` 模式在 rag/composables/ 内实现 kb 专用版（该 composable 耦合 complaintId/TaskStatus 不可直接复用；通用化下沉 shared 不在本批）。
- 按钮级权限以 §5.0 F 后端 allowlist 字面值为准：上传/删除/重建/摘要编辑/词库刷新后端词表 `ADMIN/SUPERVISOR`，前端对 ai_trainer 置灰 + 悬浮说明，置灰不发起请求；`meta.roles` 仅控菜单可见性（§11.1）。
- 页面范围（对齐 §5.4 功能树，Gradio 平移逻辑注明来源行号）：
  - KbDocuments：PageTable 列表（分类映射 regulation=制度法规/script=催收话术/case=案例/other=其他、status 枚举 active/processing/failed/archived，来源 gradio/formatters.py:22-31；列：标题/分类/status/stage/chunks/创建时间/doc_id，来源 formatters.py:36）；行点击 ElDrawer 详情（含 ingest_stage/ingest_progress/ingest_error/assets 元数据；shared 无 DetailDrawer 实体，本批不新封公共组件）；摘要编辑对话框（≤200 字 rules 校验）；删除/重建二次确认（重建确认框注明「仅按正文重建，不补全表格/图片资产」，README §G reindex 行）。
  - 上传对话框：14 种扩展名白名单（app.py:45-48）本地校验 + 分类 + 可选摘要；本地预览浏览器侧仅做 txt/md（FileReader）/图片（object URL）/PDF（object URL iframe），xlsx/docx/pptx/音频提示「该格式无内嵌预览」，不引 SheetJS 等新依赖。
  - KbSearch 检索测试台：参数面板（query 多行 1~500 字、top_k Slider 1-20 默认 5、分类下拉含「全部」、rewrite 开关默认关）；结果卡片展示 content/title/chunk_index/score/rerank_score（score 只展示不二次计算，§17 术语表）；degraded/message 警示徽章；trace_id 用 TraceIdText 可复制；空结果以 `results.length===0` 判定空态（§15.1 A3004 注）。
  - KbSensitive：current.dict 等宽纯文本只读展示 + 复制（text/plain 非信封，走 request.ts:90 TEXT_WHITELIST 既有白名单，拦截器零改动；404 → 空态「词典暂不可用」）；刷新按钮二次确认后展示新 version；页面静态说明「敏感词无 CRUD 接口」（§5.0 注 2）。
- 砍项（相对 Gradio 演示）：健康检查条（f2-qc-console 先例，生产健康归运维监控）；会话管理/X-Session-Id/prev_queries（answer=false 无多轮语义）；命中图片 Gallery/命中表格预览（依赖 Gradio 与后端共享 KB_ASSETS_DIR 卷，前端无通道，§5.4 检索测试台不要求）；在线问答/引用气泡（answer=true 属 F2b，阻塞于后端 `^/api/v1/kb/search$` 120s 正则 location，本批不实现）；上传前前端重名预检与 600s 超时降级卡片（后端已 B0001 防重 + 异步入库早返回，语义被新契约取代）。
- 错误处理严格按 §15.1 白名单（禁止新增映射）：B0001 表单行内标红（透出后端 message）、B0003 403 提示不重试、B0004 详情空态+返回列表、B0006 按 Retry-After 退避（限流字面值：list/detail 60、search 200、upload 20、reindex/summary/refresh 10）、B0007/B0008/B0009 兜底文案+trace_id+重试按钮；A3001/A3002 走默认分支 message 直出+trace 后 8 位；rag 服务 BizException 一律 HTTP 400，前端一律按信封 code 判定成败。
- 契约核实（types.ts 逐字段来源行号记录）：9 端点请求/响应字段以 rag-service 源码为准（documents.py:58/126/149/193/226/354、search.py:53、models/search.py:21-91、sensitive_words.py:28/49）。
- 已知风险写入 design：reindex 同步长任务经网关 15s 大文档可能超时/504——前端 12s 超时后提示「操作已提交，请刷新列表查看 status 终态」，不自动重试；网关是否放宽留待后端评估，本批不动。
- SSOT 闭环（§13）：tasks 第一批复核上传阻塞依赖——异步入库 BREAKING 后 §5.0 D 表/§9.3「上传需后端放宽至 60s+」可能过时；若确认解除，回填 §5.0 D 表上传行、§9.3 矩阵与附录 B.2（版本号递增、变更登记）；若仍成立（大文件请求体传输受网关限制），保留阻塞登记并在 design 写明。
- 不引入 ECharts/SSE/WebSocket/SheetJS 等任何新运行时依赖；不改 rag-service 后端与网关；不改其他四模块；shared/ 与 app/ 既有逻辑零改动；不建模块 store（§10：列表条件/分页组件本地 ref，qc QcRecordList 先例）。

## Capabilities

### New Capabilities

- `frontend/rag-console`: RAG 知识库管理台前端模块——文档管理（列表/详情/异步入库轮询/摘要编辑/删除/重建索引）、检索测试台（answer=false 混合检索调试）、敏感词词典查看/刷新，契约以 SSOT §5.0 D 表 9 端点与 rag-service 源码为准。

### Modified Capabilities

（无既有 capability 的需求级变更。）

## Impact

- **前端**：新增 `frontend/src/modules/rag/`（约 3 视图 + 若干组件/composable）；`app/__tests__/route-matrix.spec.ts` 补 2 条路由断言；`src/mocks/browser.ts` 新增 9 端点 mock（**axios 自定义 adapter 实现**——`package.json` 无 msw 且本批禁新增依赖，能力等价，见 design D8；DEV+VITE_USE_MOCK 双重门控）。
- **类型声明**：`frontend/src/vite-env.d.ts` 新增 `VITE_USE_MOCK?: string` 环境变量类型（+6 行，纯类型声明、无运行时代码）。
- **文档**：SSOT《frontend/前端架构.md》§5.0 D 表/§9.3/附录 B.2 视契约核实结果回填（§13 闭环）。
- **后端/网关/shared/ 零改动；app/ 既有逻辑零改动**（唯一例外：`app/__tests__/route-matrix.spec.ts` 断言随路由表同步更新，qc 先例）。
- **依赖**：无新增运行时依赖。
- **后续批次**：F2b 在线问答（answer=true）阻塞于后端 `^/api/v1/kb/search$` 120s 正则 location，放行后单独变更交付。

# Spec Delta

## Purpose

frontend/rag-console 是 RAG 知识库的管理台前端能力（F2）：将 Gradio 调试工作台（services/rag-service/gradio/）平移进 Vue 3 管理台，覆盖文档管理（列表/上传/详情/摘要/删除/重建索引）、检索测试台（answer=false）、敏感词词典查看/刷新，契约以 SSOT《frontend/前端架构.md》§5.0 D 表 9 端点与 rag-service 源码为准。

## ADDED Requirements

### Requirement: RAG 模块路由必须按 SSOT §11.1 装配

模块 MUST 以 BizModule 自包含结构落 3 条路由替换 F0 占位：`/kb/documents`（KbDocuments）、`/kb/search`（KbSearch）、`/kb/sensitive-words`（KbSensitive）；`meta.roles` 均 MUST 为 `['admin','ai_trainer']`，keepAlive 均 MUST 开启；视图 MUST 懒加载且 `defineOptions` name 与路由 name 逐字一致；`app/router/index.ts` 装配行 MUST 零改动（ragModule 已挂载）。

#### Scenario: 占位路由被替换且可插拔

- **WHEN** 模块实现完成并执行可插拔反向验证（临时删除 `modules/rag` + 装配行）
- **THEN** `vue-tsc --noEmit && vite build` 仍通过；恢复后 3 条路由按 §11.1 逐字出现在路由表与 route-matrix.spec.ts 断言中

#### Scenario: ai_trainer 可见、其他角色被拦截

- **WHEN** agent/qc_reviewer 角色用户直接访问 `/kb/documents`
- **THEN** 全局守卫拦截至 `/403`；admin/ai_trainer 可正常进入

### Requirement: API 层必须逐条对齐 §5.0 D 表且只走全前缀

模块 api.ts MUST 且仅 MUST 接入 §5.0 D 表 9 端点（一律 `/api/v1` 根绝对前缀）：documents 列表/上传/详情/删除/重建索引/摘要编辑、kb/search、sensitive-words current.dict/refresh；全部端点 MUST 使用 `TIMEOUT.normal`，模块内 MUST NOT 出现超时数值字面量；types.ts 字段 MUST 与 rag-service 源码逐字段对齐并注释来源行号。

#### Scenario: 契约逐条对照通过

- **WHEN** 验收时对照 §5.0 D 表逐条核对 api.ts 调用
- **THEN** 9 条 method+path 全数命中（documents.py:58/126/149/193/226/354、search.py:53、sensitive_words.py:28/49），无表外调用、无缺少

#### Scenario: 分页参数钳制

- **WHEN** 文档列表请求携带 page_size
- **THEN** 默认 20、上限 100（documents.py:131-136），超出由前端钳制，不依赖后端报错

### Requirement: 上传必须为异步入库并轮询至终态

上传 MUST 按异步入库契约（README §G BREAKING / documents.py:66）实现：校验通过即返回 `{doc_id, status:"processing"}`；前端 MUST 轮询列表直至 `active` 或 `failed`（终态自停、组件卸载清理定时器、连续失败熔断）；进度 MUST 按 stage→progress 映射展示（parse=25/body_index=50/asset_index=75/done=100）；`failed` 时 MUST 展示 `ingest_error`；前端 MUST NOT 做重名预检（后端 B0001 防重，message 直接透出）。

#### Scenario: 上传成功后轮询至 active

- **WHEN** 用户选择合法文件上传成功
- **THEN** 列表立即出现 processing 行，进度条按 stage 映射推进，终态 active/failed 后轮询自动停止

#### Scenario: 重名上传被拒

- **WHEN** 同名文档处于 processing/active 时再次上传
- **THEN** 后端 B0001 message 在表单行内透出（如「同名文件正在入库中…」），不弹全局 toast，不发起并行入库

#### Scenario: 组件卸载后无残留定时器

- **WHEN** 轮询进行中用户离开页面
- **THEN** 定时器被清理，无后台请求继续发出

### Requirement: 上传前本地预览必须按格式分档

本地预览 MUST 在浏览器侧实现且 MUST NOT 引入新运行时依赖：txt/md 用 FileReader 文本预览、图片用 object URL、PDF 用 object URL iframe；xlsx/docx/pptx/音频 MUST 显示「该格式无内嵌预览」；扩展名白名单 MUST 为 14 种（txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma，app.py:45-48），白名单外本地拦截不发起请求。

#### Scenario: 各格式预览分档正确

- **WHEN** 用户依次选择 md、png、pdf、xlsx、exe 文件
- **THEN** md 显示文本、png 显示图片、pdf 内嵌预览、xlsx 提示无内嵌预览、exe 被本地拦截并提示支持格式清单

### Requirement: 文档详情与维护操作必须如实呈现后端语义

详情 MUST 以抽屉呈现 documents.py:149 全字段（含 ingest_stage/ingest_progress/ingest_error/assets 元数据）；摘要编辑 MUST ≤200 字前端校验（KB_SUMMARY_MAX_LEN）；删除与重建索引 MUST 二次确认；重建确认框 MUST 注明「仅按正文重建，不补全表格/图片资产」（README §G reindex 行），MUST NOT 暗示重建补全资产；重建为同步长任务，超时后 MUST 提示「操作已提交，请刷新列表查看 status 终态」且不自动重试。

#### Scenario: 重建索引确认框文案如实

- **WHEN** admin 点击重建索引
- **THEN** 确认框明确说明仅按 PG 正文重切、assets_rebuilt=false、存量表格增强需删除后重传

#### Scenario: 摘要超长前端拦截

- **WHEN** 摘要输入超过 200 字提交
- **THEN** 表单 rules 行内标红，请求不发出

### Requirement: 检索测试台必须固定 answer=false 并展示召回明细

检索请求 MUST NOT 携带 `answer` 字段（固定 false 语义）；参数面板 MUST 提供 query（1~500 字）、top_k（1~20 默认 5）、分类（含「全部」）、rewrite（默认关）；结果 MUST 以卡片展示 content/title/chunk_index/score/rerank_score，score MUST 只展示不二次计算；`degraded=true` 与 `message` MUST 以警示徽章呈现；trace_id MUST 可复制；空结果 MUST 以 `results.length===0` 判定空态「未找到相关内容」（§15.1 A3004 注，后端不抛该码）。

#### Scenario: answer 字段不下发

- **WHEN** 用户发起任意检索
- **THEN** 请求体仅含 query/top_k/category?/rewrite，无 answer 键（单测断言）

#### Scenario: 兜底降级可见

- **WHEN** 检索触发双闸门兜底
- **THEN** 结果区顶部出现 degraded 警示徽章与后端 message 原文

### Requirement: 敏感词页必须只读展示词典并提供刷新

词典 MUST 以等宽纯文本只读展示（GET current.dict，text/plain 非信封，走既有 TEXT_WHITELIST，拦截器零改动）；404 时 MUST 空态「词典暂不可用」；刷新 MUST 二次确认并展示返回的新 version；页面 MUST 静态说明「敏感词无 CRUD 接口，仅词典查看与版本刷新」（§5.0 注 2），MUST NOT 出现列表/新增/编辑入口。

#### Scenario: 词典加载与刷新

- **WHEN** admin 进入页面并点击刷新
- **THEN** 文本域展示当前词典；二次确认后展示新版本号

#### Scenario: 词典缺失空态

- **WHEN** current.dict 返回 404
- **THEN** 页面展示空态文案而非错误弹窗

### Requirement: 按钮级权限必须按后端 allowlist 对 ai_trainer 置灰

上传/删除/重建索引/摘要编辑/词库刷新按钮（后端词表 ADMIN/SUPERVISOR，§5.0 F）MUST 对 ai_trainer 置灰并悬浮说明「该操作需 admin/supervisor 权限」；置灰状态下 MUST NOT 发起请求；`meta.roles` 仅控菜单可见性，不承担授权职责（§11.1 权限边界声明）。

#### Scenario: ai_trainer 只读体验

- **WHEN** ai_trainer 打开文档管理与敏感词页
- **THEN** 全部写操作按钮置灰带说明，浏览/检索功能正常，网络面板无写请求发出

### Requirement: 错误处理必须统一走信封与白名单

模块 MUST 一律按信封 code 判定成败（rag 服务 BizException HTTP 400 不得视为传输异常）；错误处置 MUST 严格按 §15.1 白名单且 MUST NOT 新增映射：B0001 表单行内标红透出后端 message、B0003 403 提示不重试、B0004 详情空态+返回列表、B0006 按 Retry-After 退避、B0007/B0008/B0009 兜底+trace_id+重试；A3001/A3002 MUST 走默认分支 message 直出+trace 后 8 位。

#### Scenario: 限流退避

- **WHEN** 触发限流（如 refresh 超 10/min）
- **THEN** 提示「系统繁忙，稍后重试」并按 Retry-After 展示退避时长，拦截器不自动重试

#### Scenario: 白名单零新增

- **WHEN** error-map.spec.ts 执行
- **THEN** 断言 §15.1 表无新增映射，A3001/A3002 命中默认分支

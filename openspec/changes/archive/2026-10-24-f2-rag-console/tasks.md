# Tasks

## 1. 契约核实与 SSOT 回填（§13 闭环）

- [x] 1.1 逐字段核实 9 端点契约并记录来源行号：documents.py:58（上传 Form file/category/summary → {doc_id,status}）、documents.py:126（列表参数/响应 items/total/page/page_size）、documents.py:149（详情全字段含 ingest_*/assets）、documents.py:193（删除）、documents.py:226（reindex）、documents.py:354（摘要 PUT {summary}）、search.py:53 + models/search.py:21-91（SearchRequest/SearchResponse）、sensitive_words.py:28/49（current.dict text/plain、refresh {version}）
- [x] 1.2 复核上传阻塞依赖：对照 README §G BREAKING（异步入库早返回）与 SSOT §5.0 D 表上传行、§9.3 矩阵「需后端放宽至 60s+」登记，判定是否仍成立（大文件请求体传输受网关 client_body 限制场景）
- [x] 1.3 若 1.2 判定解除：回填 §5.0 D 表上传行、§9.3 矩阵、附录 B.2（版本号递增 + 变更登记）；若仍成立：在 design.md 保留阻塞登记并注明复核结论

## 2. types.ts + api.ts

- [x] 2.1 types.ts：按 1.1 核实结果定义 9 端点请求/响应 interface，逐字段注释源码行号；分类（regulation/script/case/other）与状态（active/processing/failed/archived）枚举对齐后端，不发明枚举（SSOT 元规则②）
- [x] 2.2 api.ts：9 条调用一律 `/api/v1` 全前缀、`TIMEOUT.normal`、无超时字面量（§9.3）；列表 page_size 默认 20、上限 100 前端钳制（documents.py:131-136）；上传用 FormData 组装 file/category/summary
- [x] 2.3 current.dict 调用声明 text 响应类型，走 request.ts:90 TEXT_WHITELIST 既有分支，拦截器零改动（§9.1）

## 3. 路由替换

- [x] 3.1 routes.ts：落 /kb/documents（KbDocuments）、/kb/search（KbSearch）、/kb/sensitive-words（KbSensitive），meta.roles=['admin','ai_trainer']、keepAlive=true、懒加载；index.ts 默认导出 BizModule {name:'rag', routes}（§7.1、§11.1）
- [x] 3.2 route-matrix.spec.ts：补 /kb/search、/kb/sensitive-words 断言（/kb/documents 已有），角色矩阵逐格一致
- [x] 3.3 反向验证：临时删除 modules/rag + router/index.ts 装配行，vue-tsc --noEmit && vite build 通过；随后恢复（§7.2）

## 4. KbDocuments 文档管理（含上传轮询）

- [x] 4.1 列表页：PageTable + 分类/状态筛选 + 分页，列=标题/分类/status/stage/chunks/创建时间/doc_id（formatters.py:36），映射表对齐 formatters.py:22-31；组件本地 ref 管状态（D5）；defineOptions name 逐字一致 + onActivated 刷新/onMounted 去重（§11.1 注）
- [x] 4.2 详情 ElDrawer：展示 documents.py:149 全字段（ingest_stage/ingest_progress/ingest_error/assets 元数据）（D3）
- [x] 4.3 摘要编辑对话框：≤200 字 rules 校验（KB_SUMMARY_MAX_LEN），PUT documents.py:354
- [x] 4.4 删除/重建二次确认；重建确认框注明「仅按正文重建，不补全表格/图片资产」（README §G reindex 行）；reindex 超时后提示「操作已提交，请刷新列表查看 status 终态」不自动重试（D7）
- [x] 4.5 上传对话框：14 扩展名白名单本地校验（app.py:45-48）+ 分类 + 可选摘要；本地预览四档分档（txt/md FileReader、图片/PDF object URL，PDF 超 8MB 不预览；xlsx/docx/pptx/音频提示无内嵌预览）（D4）
- [x] 4.6 rag/composables/ kb 轮询：参照 complaint/composables/usePolling.ts 模式（终态自停/unmount 清理/失败熔断），入参为轮询函数+终态谓词，不硬编码业务字段（D2）；上传成功后轮询列表至 active/failed，进度条按 parse=25/body_index=50/asset_index=75/done=100 映射，failed 展示 ingest_error（README §G）

## 5. KbSearch 检索测试台

- [x] 5.1 参数面板：query 多行 1~500 字、top_k Slider 1-20 默认 5、分类含「全部」、rewrite 默认关（models/search.py:21-35）；请求体禁带 answer 键
- [x] 5.2 结果卡片：content/title/chunk_index/score/rerank_score，score 只展示不二次计算（§17）；degraded/message 警示徽章；trace_id 用 TraceIdText 可复制；空结果 results.length===0 空态（§15.1 A3004 注）

## 6. KbSensitive 敏感词页

- [x] 6.1 等宽纯文本只读展示 current.dict + 复制；404 → 空态「词典暂不可用」（sensitive_words.py:41-46）
- [x] 6.2 刷新按钮二次确认 → 展示新 version（sensitive_words.py:49-63）；页面静态说明「敏感词无 CRUD 接口」（§5.0 注 2）

## 7. 按钮级权限

- [x] 7.1 上传/删除/重建/摘要编辑/词库刷新对 ai_trainer 置灰 + 悬浮说明「该操作需 admin/supervisor 权限」，置灰不发起请求（§5.0 F 表、§11.1 权限边界声明）

## 8. MSW mock 与单测

- [x] 8.1 src/mocks/ 新增 handlers：9 端点信封 + current.dict text/plain；DEV+VITE_USE_MOCK 门控（§16.1）
- [x] 8.2 api.spec.ts：9 端点路径/参数/page_size 钳制/FormData 组装
- [x] 8.3 KbDocuments 交互：轮询终态自停与 unmount 清理、删除/重建二次确认、ai_trainer 置灰、摘要 200 字校验
- [x] 8.4 KbSearch：answer 字段不下发、空态判定、degraded 徽章、top_k 边界
- [x] 8.5 KbSensitive：text/plain 渲染、404 空态、刷新 version 展示、ai_trainer 刷新置灰
- [x] 8.6 error-map.spec.ts：断言未新增映射，A3001/A3002 走默认分支（§15.1）

## 9. 验收与回填

- [x] 9.1 对照 §5.4 功能树与 §5.0 D 表逐条 method+path 核对；CI frontend job（eslint + vue-tsc + depcruise + vitest）全绿（§16）
- [x] 9.2 输出逐项 pass/fail 对照表（标注证据）；回填附录 B.2

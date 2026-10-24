# Design

## Context

当前状态：`frontend/src/modules/rag/` 仅占位路由（routes.ts → ModulePlaceholder）；qc 模块已按完整结构落地（api/types/views/components/composables/__tests__），为本批提供同构模板。底座机制均已就绪：shared/request 单实例（信封解包/isSuccess/TEXT_WHITELIST 含 current.dict，request.ts:90）、TIMEOUT 常量表（§9.3）、路由守卫与 keepAlive 机制（§11/§11.1 注）、shared base 组件 PageTable/StatusTag/TraceIdText、error-map 白名单（§15.1）。约束：本批不改后端/网关/shared//app/，无新运行时依赖。

## Goals / Non-Goals

**Goals:**
- 3 视图 + api/types/composables 的 rag 模块，逐条对齐 §5.0 D 表 9 端点
- 异步入库轮询闭环（processing → active/failed，进度映射、终态自停、卸载清理、失败熔断）
- ai_trainer 只读体验（按钮置灰 + 说明，不发请求）
- 可插拔反向验证通过；CI frontend job 全绿

**Non-Goals:**
- F2b 在线问答（answer=true，阻塞于后端 120s location）；敏感词 CRUD；命中资产预览
- usePolling 通用化下沉 shared（留待第三个使用者或专门批次）；DetailDrawer 新封公共组件
- 模块 store（§10 无跨页共享需求）

## Decisions

**D1｜模块结构照搬 qc 同构模板。** views/ 容器（数据/编排）+ components/ 展示（props/emit）+ composables/ + api.ts/types.ts。理由：qc 模块已通过 F2 验收，同构降低评审与维护成本；替代方案（自创新结构）无收益且破坏 §7 一致性。

**D2｜kb 轮询在 rag/composables/ 内实现，不下沉。** complaint 的 usePolling（complaint/composables/usePolling.ts:42）签名耦合 `complaintId: Ref<string>` 与 complaint 域 TaskStatus，不可直接复用。本批参照其模式（3s 首轮退避/终态自停/unmount 清理/连续失败熔断/document.hidden 暂停）实现 kb 专用轮询（入参为轮询函数 + 终态谓词，不硬编码业务字段）。§17 下沉规则要求「第二个真实使用者出现再下沉」，但通用化需重构 complaint 侧签名并回归全模块，超出本批边界；design 登记为后续项。

**D3｜文档详情用 ElDrawer 模块内实现。** shared/components/base 无 DetailDrawer 实体（§8 仅为规划），本批不新封公共组件（§12「不滥封」）；详情为页内抽屉而非独立路由（§11.1 路由表无 /kb/documents/:id 行，不擅自加路由）。

**D4｜上传预览四档分档，零新依赖。** txt/md→FileReader；图片/PDF→object URL（iframe）；xlsx/docx/pptx/音频→「无内嵌预览」提示。理由：预览仅为上传前辅助确认，SheetJS 等依赖不值其成本；Gradio 对 pptx/音频本就无预览（formatters.py:43），体验对齐旧演示。PDF 超 8MB 不预览（对齐 formatters.py:40 PDF_PREVIEW_MAX，防内存尖峰）。

**D5｜列表状态组件本地 ref。** 照搬 QcRecordList 先例（rows/total/page/pageSize/loading/error 本地 ref + onActivated 刷新/onMounted 首激活去重）；§8 规划的 useTable/usePagination 无实体，本批不创建。

**D6｜检索测试台不启用 trace 上下文注入。** X-Trace-Id 拦截器复用零改动；检索为单次调试动作，无「查看→操作」复合链路（§9.4 语境），仅展示响应 trace_id（TraceIdText）。理由：避免为无串联需求引入模块级 trace store。

**D7｜reindex 超时处置为「引导刷新」而非网关放宽。** 12s 超时/504 后提示「操作已提交，请刷新列表查看 status 终态」，不自动重试；后端 reindex 已将文档置 processing（documents.py:260），刷新列表即可见终态。网关是否放宽由后端另行评估（§9.3 矩阵不动）。

**D8｜开发态 mock 以 axios 自定义 adapter 实现（msw 未安装，本批不新增依赖）。** 置于 `src/mocks/browser.ts`：覆盖 9 端点 + current.dict 文本响应，信封结构与后端 success_response 对齐；current.dict 返回 text/plain 以覆盖 TEXT_WHITELIST 分支；未命中 handler 的请求透传原 adapter 走真实网络（登录等不受影响）。门控为 `app/main.ts` 的 `import.meta.env.DEV` 动态引入 + `VITE_USE_MOCK === 'true'` 双重门控，生产构建不打包。**偏差登记**：原方案写 MSW，核实 `package.json` 无 msw（仅 optional peer），且本批禁新增依赖，故以等价 adapter 实现；能力等价（拦截/假数据/透传/门控），差异仅在实现载体。

## Risks / Trade-offs

- [reindex 同步长任务经网关 15s，大文档可能超时/504] → D7 引导刷新；tasks 首批复核并在验收记录实际表现；网关放宽列为后端待办，本批不动
- [上传「60s+ 放宽」阻塞依赖可能过时（异步入库后响应即时，仅大文件请求体传输仍受网关限制）] → tasks 批次①逐行复核：解除则走 §13 闭环回填 §5.0 D 表/§9.3/附录 B.2；仍成立则保留阻塞登记
- [kb 轮询与 complaint usePolling 两份实现并存，存在漂移风险] → 本批注释互相引用来源；第三个使用者出现时优先通用化下沉（D2 后续项）
- [text/plain 白名单为单 URL 精确匹配，未来 rag 新增文本端点不复用] → 本批无新增；新增时需走 §13 白名单评审

## Migration Plan

纯前端增量：占位路由 → 真实视图，无数据迁移。回滚 = 恢复 `modules/rag/routes.ts` 占位版本（ModulePlaceholder），其余文件删除即可；可插拔反向验证（§7.2）天然覆盖回滚路径。灰度：管理台按角色菜单可见性天然灰度（admin/ai_trainer），无全量用户面。

## Open Questions

- F2b 在线问答放行时点：取决于后端 `^/api/v1/kb/search$` 120s location 排期（不阻塞本批）。

# RAG Gradio Frontend Specification

## Purpose

提供 RAG 知识库的浏览器可视化入口（Gradio 独立进程/服务），支持业务方直接在浏览器上传文档、可视化"问题 → 召回片段（含图片/表格预览）→ LLM 回答"全链路，降低知识库使用与验收门槛。

## Requirements

### Requirement: 浏览器访问与页面可用

系统 SHALL 提供 Gradio 浏览器前端（独立 compose 服务，端口 7860，仅内部网络使用、不接公网），包含上传页与问答页。浏览器打开页面 MUST 正常渲染、无 JS 报错、可正常交互。

#### Scenario: 浏览器打开页面正常

- **WHEN** 用户在浏览器访问 Gradio 页面地址
- **THEN** 页面正常打开，上传页/问答页可切换，无控制台报错，输入与按钮可交互

### Requirement: 网页上传文档/音频

系统 SHALL 支持在网页上传全部支持格式（txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma），上传时 MUST 调用后端 `POST /kb/documents`（携带文件与 category）。

**异步成功语义**：当后端返回 `doc_id` 且 `status=processing` 时，页面 MUST 立刻展示 doc_id 与 processing，并引导用户刷新文档列表查看进度（`ingest_stage`/`ingest_progress`/`ingest_error` 若返回则展示）；列表表格 SHOULD 含 status 与 ingest_stage 列。MUST NOT 把「等完全程 active」当作上传成功的唯一条件。最终 `active`/`failed` 以列表/详情为准。

上传失败（校验失败、业务错误码）MUST 友好提示，**不得白屏、不得未捕获 500**。`status=failed` 或 `degraded=true` 的结果 MUST 醒目展示为失败/降级，不得看起来像普通成功以免催重复传。

传输失败分流（保持既有、禁止回退）：

- **连接失败**：提示「后端服务暂不可用」；
- **读超时且请求已发出**：提示入库处理中，引导刷列表看 processing/active，并提示勿重复上传；MUST NOT 提示「后端服务暂不可用」。

#### Scenario: 上传成功返回 doc_id

- **WHEN** 用户在网页选择上传一个 TXT 文件并选择分类后点击上传
- **THEN** 页面调用后端 ingest 接口成功，展示返回的 doc_id 与 status（可为 processing；最终 active 以列表/详情为准），不以未返回 active 判定为上传失败

#### Scenario: 上传返回 processing 立刻展示

- **WHEN** 用户上传合法文件且后端返回 doc_id + status=processing
- **THEN** 页面立即展示 doc_id 与 processing，并提示可刷新列表查看进度；不以未返回 active 判定为上传失败

#### Scenario: 上传失败页面友好提示

- **WHEN** 用户上传不支持格式或后端返回业务错误
- **THEN** 页面展示友好错误提示（含错误码/原因，无裸栈），不白屏

#### Scenario: 后端不可用时上传友好降级提示

- **WHEN** 连接失败、拒绝连接、DNS 失败等
- **THEN** 页面提示「后端服务暂不可用」，不白屏、不卡死

#### Scenario: 上传读超时按入库处理中提示

- **GIVEN** 上传请求已发往后端
- **WHEN** 发生读超时
- **THEN** 提示入库处理中并引导刷列表、勿重复上传；MUST NOT 提示「后端服务暂不可用」

#### Scenario: failed 或 degraded 醒目展示

- **WHEN** 上传结果或列表显示 status=failed 或 degraded=true
- **THEN** 页面以醒目失败/降级样式展示，不得与普通成功 active 混淆

### Requirement: 问答全链路可视化

系统 SHALL 支持在问答页输入问题并展示完整链路：问题 → 召回 chunk（含图片/表格预览与溯源元数据）→ LLM 回答（开关开启且请求开启时）。全链路结果 MUST 完整展示；异常（后端宕机/LLM 熔断）MUST 友好提示、不阻塞页面。

#### Scenario: 问答展示全链路结果

- **WHEN** 用户在问答页输入"逾期协商需要哪些材料"并触发检索
- **THEN** 页面依次展示问题、召回 chunk 列表（含 content、score、溯源信息、图片/表格预览）、以及 LLM 回答（开关开启时），全链路信息完整

#### Scenario: 召回片段展示溯源元数据

- **WHEN** 问答返回召回片段
- **THEN** 每个召回片段可见 doc_id、来源 source、页码 page 等溯源元数据，图片/表格类命中片段可见预览

#### Scenario: LLM 熔断时问答不阻塞

- **WHEN** LLM 服务不可用（熔断），问答页请求检索
- **THEN** 页面展示召回片段与"LLM 回答暂不可用"友好提示，页面不白屏、不阻塞后续操作

#### Scenario: 后端宕机时问答友好降级

- **WHEN** 后端服务宕机，问答页发起检索
- **THEN** 页面展示友好错误提示，不白屏、不出现未捕获 500

### Requirement: 预览链路与调用鉴权

- **预览链路**：内嵌图片/表格预览源为共享数据卷——Gradio 与 rag-service MUST 共享 `KB_ASSETS_DIR`；**MUST** 通过 `GET /kb/documents/{id}` 响应 `assets` 获取 `storage_path`，再读盘渲染 image；表格按 content_json 渲染；**不引入新后端静态公网下载端点**。
- **上传页/列表侧**：文档抽出 image 资产后，上传页或列表选中文档 MUST 能预览该 doc 的 image 资产（不仅限问答召回命中）。
- **鉴权**：调用 `/kb/*` MUST 携带 `RAG_API_TOKEN`；未配置时友好提示「后端服务暂不可用/未配置访问凭证」。

#### Scenario: 共享数据卷内预览源可读

- **WHEN** 问答返回含图片/表格 assets 的召回片段
- **THEN** Gradio 通过共享数据卷按 storage_path 读取图片文件并渲染 `gr.Image`，按 storage_path 指向的 content_json 文件渲染表格预览，预览正常显示

#### Scenario: 上传页或列表预览 doc 图片资产

- **GIVEN** 文档已抽出至少一张 image 资产，`GET /kb/documents/{id}` 返回非空 `assets`
- **WHEN** 用户在上传页/列表侧选中并查看该文档
- **THEN** Gradio 按 `assets[].storage_path` 读共享盘渲染 image；不依赖新建公网静态下载 API

#### Scenario: failed 文档列表展示 ingest_error

- **WHEN** 列表中存在 status=failed 的文档且详情/列表含 `ingest_error`
- **THEN** 页面展示失败原因字段，不得仅显示 failed 而无说明

#### Scenario: 未配置访问 token 时友好降级

- **WHEN** 未注入 RAG_API_TOKEN，用户触发上传/检索
- **THEN** 页面友好提示未配置凭证，不白屏、不裸 500

### Requirement: 复用后端契约与部署约束

Gradio 前端 MUST 以既有 `/kb/*` API 为主进行编排；**允许**消费本变更引入的异步 processing 响应、进度字段与按文档读取资产元数据所需的既有/增量字段，**不得**为预览新建公网静态下载 API。检索 `answer=true` 与 `KB_LLM_ANSWER_ENABLED` 对齐规则不变。前端不实现检索/入库核心业务逻辑（纯展示与调用编排）。

#### Scenario: 前端复用既有 API 且不动后端契约

- **WHEN** 审计 Gradio 前端的调用依赖
- **THEN** 前端仅调用 `/api/v1/kb/*` 既有与本变更增量字段，未引入公网静态下载端点、未擅自修改无关接口路径

#### Scenario: 前端消费异步与进度字段

- **WHEN** 审计 Gradio 上传/列表调用
- **THEN** 前端正确处理 status=processing 与进度字段，并仍主要调用 `/api/v1/kb/*`，未引入公网静态下载端点

#### Scenario: 独立服务部署与健康检查

- **WHEN** 部署环境启动 Gradio
- **THEN** Gradio 作为独立 compose 服务运行，依赖 rag-service healthy 后启动，可独立启停

### Requirement: 重复上传防护（Gradio）

当同一文件仍在 `processing` 时，Gradio MUST 阻止或强烈提示勿重复上传（至少检测进行中同名；有条件检测同 hash）。MUST NOT 在无提示下静默对同一文件开多条并行入库。

#### Scenario: 同名 processing 再点上传

- **GIVEN** 列表中已有同名文件 status=processing
- **WHEN** 用户再次选择同名文件点击上传
- **THEN** 页面阻止或强烈提示；不得静默再发第二条入库请求

### Requirement: reindex 超时与资产警告

Gradio 对 reindex：`POST .../reindex` **保持同步**（本变更不异步化 reindex）。Gradio MUST 使用与 upload 同级读超时（如 600s）；超时后按「处理中」语义引导轮询列表/详情 status，**MUST NOT** 因原 90s 读超时提示「后端服务暂不可用」。若 reindex 为仅正文语义，页面 MUST 在确认前或结果中强提示图/表资产可能清除且未完整恢复，并推荐重新上传。

#### Scenario: reindex 不再假报后端不可用

- **WHEN** 用户触发 reindex 且 HTTP 读超时但后端可能仍在处理
- **THEN** 页面按处理中/轮询语义提示并引导刷列表，MUST NOT 仅因读超时提示「后端服务暂不可用」

#### Scenario: 仅正文 reindex 强警告

- **WHEN** 用户对含资产文档确认 reindex（默认仅正文语义）
- **THEN** 页面展示资产可能丢失/未恢复的强警告

### Requirement: Gradio 操作锁粒度

Gradio MUST NOT 使用进程级单一 `_inflight` 导致「任意一人上传则全员操作被挡」。锁粒度 MUST 按操作类型 + 文件/用户（或会话）区分，或在异步轮询模式下允许列表刷新与其他用户操作并行。

#### Scenario: 一人上传不阻塞他人刷列表

- **GIVEN** 用户 A 正在上传
- **WHEN** 用户 B（或同一工作台另一操作）刷新文档列表
- **THEN** 列表刷新可用，不得统一报「正在处理上一请求」而无法刷进度

### Requirement: 检索请求使用本轮原句

Gradio 问答发往 `POST /kb/search` 的 `query` MUST 等于用户本轮输入原文（去首尾空白后），MUST NOT 把最近若干轮问题用空格拼接成一条检索 query。调试区可以展示历史轮次，但展示用字符串 MUST NOT 被当作检索 query。后端 query rewrite 开关与行为不在本 Requirement 范围内。

#### Scenario: 同 session 连续两问不拼接

- **GIVEN** 同一会话已问「RAG多模态全局知识库系统 角色定位是什么」
- **WHEN** 用户再问「RAG多模态全局知识库系统 这个 技术类别 有那些？」
- **THEN** 后端收到的 query / `kb_search_logs.query` / `query_original` 等于第二问原文，不得包含「角色定位」

#### Scenario: 调试区仍可见历史

- **WHEN** 用户连续提问
- **THEN** 页面可展示历史轮次或「未拼接历史」说明，且实际请求 query 仍为本轮原句

### Requirement: 同名已入库时前端阻止再传

Gradio 上传前 MUST 检测同名 `status=active`（未删除）文档；若存在 MUST 阻止或强烈提示「同名已入库，请先删除或改文件名」，MUST NOT 在无提示下再发 POST。同名 `processing` 阻止/强提示保持不变。

processing 同名检测 MUST 穷尽匹配：不得只读取 `page_size=100` 的第一页后漏检。允许按 title 过滤、翻页直至穷尽，或调用后端已有/新增的按 title 查询。

#### Scenario: 同名 active 再点上传

- **GIVEN** 列表中已有同名文件 status=active
- **WHEN** 用户再次选择同名文件点击上传
- **THEN** 页面阻止或强烈提示；不得静默发出第二条入库请求

#### Scenario: 同名 processing 不在第一页仍能拦住

- **GIVEN** status=processing 的同名文档不在列表第一页（或超过单页 100 条）
- **WHEN** 用户上传同名文件
- **THEN** 页面仍阻止或强烈提示；不得因只扫第一页而漏拦


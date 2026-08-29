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

系统 SHALL 支持在网页上传全部支持格式（txt/md/pdf/docx/xlsx/pptx/png/jpg/jpeg/mp3/wav/m4a/amr/wma），上传时 MUST 调用后端 `POST /kb/documents` ingest 接口（携带文件与 category）；上传成功 MUST 展示返回的 doc_id 与入库状态；上传失败 MUST 在页面友好提示错误原因（如解析失败 A3001、文件超限 B0001），**不得白屏、不得出现未捕获 500**。

#### Scenario: 上传成功返回 doc_id

- **WHEN** 用户在网页选择上传一个 TXT 文件并选择分类后点击上传
- **THEN** 页面调用后端 ingest 接口成功，展示返回的 doc_id、chunks_count 与 status=active

#### Scenario: 上传失败页面友好提示

- **WHEN** 用户在网页上传一个不支持格式的文件或后端返回业务错误
- **THEN** 页面展示友好错误提示（含错误码/原因），页面不白屏、不显示裸 500 页面

#### Scenario: 后端不可用时上传友好降级提示

- **WHEN** 后端服务不可用（连接失败/超时）
- **THEN** 页面提示"后端服务暂不可用"等友好信息，页面不白屏、不卡死

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

- **预览链路**：内嵌图片/表格预览源为**共享数据卷内文件/结构化数据**——Gradio 容器与 rag-service MUST 共享挂载 `KB_ASSETS_DIR` 数据卷，图片按 `kb_doc_assets.storage_path` 相对路径读取（`gr.Image` 渲染），表格按 `storage_path` 指向的 content_json 文件渲染 `gr.Dataframe`；Gradio 不得访问数据库或共享卷之外的存储，不引入新后端静态端点；
- **鉴权**：Gradio 调用 `/kb/*` API 时 MUST 携带内部访问 token（`RAG_API_TOKEN` env 注入，部署时由运维配置）；token 不得硬编码进前端代码/镜像；未配置 token 时页面展示"后端服务暂不可用/未配置访问凭证"友好提示。

#### Scenario: 共享数据卷内预览源可读

- **WHEN** 问答返回含图片/表格 assets 的召回片段
- **THEN** Gradio 通过共享数据卷按 storage_path 读取图片文件并渲染 `gr.Image`，按 storage_path 指向的 content_json 文件渲染 `gr.Dataframe`，预览正常显示

#### Scenario: 未配置访问 token 时友好降级

- **WHEN** 部署未注入 RAG_API_TOKEN，用户触发上传/检索
- **THEN** 页面展示"后端服务暂不可用/未配置访问凭证"友好提示，不白屏、不出现裸 500

### Requirement: 复用后端契约与部署约束

Gradio 前端 MUST 复用既有 `/kb/*` API（上传/检索），**不得修改任何后端接口契约**；检索调用携带 `answer=true` 的时机与开关 `KB_LLM_ANSWER_ENABLED` 对齐（开关关闭时前端不传 answer 或忽略空 answer）；前端不实现任何检索/入库业务逻辑（纯展示与调用编排）。

#### Scenario: 前端复用既有 API 且不动后端契约

- **WHEN** 审计 Gradio 前端的调用依赖
- **THEN** 前端仅调用既有 `/api/v1/kb/*` 接口，未引入新后端端点、未修改既有接口路径/请求/响应

#### Scenario: 独立服务部署与健康检查

- **WHEN** 部署 V5 环境
- **THEN** Gradio 作为独立 compose 服务运行（端口 7860），依赖 rag-service healthy 后启动，可通过 compose 独立启停

# 全局技术架构强制规范（AI开发必须严格落地，禁止自由架构）

【强制1】统一三层架构：Controller → Service → Repository(DAO)，单向依赖，禁止反向调用、跨层跳跃、层内混搭逻辑。
【强制2】Controller层职责锁死：仅做参数接收、参数校验、请求转发、结果组装。禁止任何业务逻辑、禁止判断、禁止计算、禁止话术生成。
【强制3】所有业务规则、状态流转、策略匹配、AI逻辑、催收流程全部收敛至 Service 层。
【强制4】Repository(DAO)层仅允许数据库CRUD，无业务判断、无复杂计算、无嵌套逻辑、无LLM调用。
【强制5】七大子系统完全解耦：data-platform(8001)、rag-service(8002)、qc-service(8003)、call-service(8004)、case-service(8005)、complaint-service(8006)、review-service(8007)。禁止跨模块直接引用代码、禁止内部耦合。
【强制6】跨模块调用必须通过统一中台网关/公共接口，禁止本地import跨模块文件。调用形态按场景分层：Agent/工作流工具取证走 MCP（或等价工具门面）；服务管理面与 CRUD 走 HTTP/JSON；异步通知走事件。MCP Server 可内部再 HTTP 调下游服务；禁止在业务节点中散落直连他服务私有实现。
【强制7】RTC实时语音、VAD静音检测、语音打断、会话状态机，必须独立工具类封装，严禁混入业务代码。
【强制8】对外部大模型 HTTP API 的调用必须走 `common.llm.transport`（或其后的共享 HTTP helper，如 `post_chat_completions` / `post_json`）发请求，禁止在业务代码中散落裸调供应商 SDK/HTTP。Transport 负责：统一 HTTP 执行、超时与错误包装。Key 解析、重试、熔断/降级由各服务 Provider（或 `common.llm.provider` / `circuit_breaker`）在 transport 之上组合；禁止理解为「transport 必须内置全部 Key/熔断逻辑」，也禁止理解为「全平台只能有一个上帝 Provider 类」。各服务可保留本域编排（Prompt、结构化 Schema、agent_role 路由、引用组装等）。
【强制8补】`common/` 仅承载跨切面共享能力（配置、安全、trace、LLM transport/reliability、Guardrails 钩子等）。**混合检索 / 知识入库 / Gradio 等 RAG 产品能力归属独立服务 `rag-service`(8002)，不得描述为 common 内的 RAG 组件库。** Agent 取法规/范本须经 MCP `rag_kb`（或等价），禁止业务图节点直连 rag HTTP。
【强制9】LLM必须具备：超时熔断、失败重试、异常降级、空结果兜底、幻觉拦截，禁止裸跑大模型。
【强制10】monorepo 根目录为 ai_collection/，7 服务代码落在 services/<service-name>/，规范在 标准工程目录结构/，禁止跨服务互相 import 内部代码。
【强制11】MVP 单组织运行，全表不加 tenant_id；多租户需求推迟，将来需要走迁移方案。
【AI开发硬性指令】生成代码后必须自查分层、依赖、耦合关系，出现跨层、跨模块耦合必须立即重构。

# 新子系统接入清单（New Service Checklist）

> 用途：在 `services/` 下从脚手架演进到业务服务时的**文档级**检查项（DEP-05 轻量交付）。  
> **不做**：替你实现 data-platform API、也不等于七系统已完备。  
> 变更：`platform-arch-unification-v5`。

## 1. 集成风格（三选一，禁止第四种私货）

| 场景 | 协议 | 示例 |
|------|------|------|
| Agent / 工作流工具取证 | **MCP**（或等价工具门面） | complaint → `rag_kb` / `call_qc` |
| 管理面 / CRUD / 人机 API | **HTTP/JSON** | `/kb/search`、后台配置 |
| 异步通知 | **事件** | 通话结束、质检完成、complaint.closed |

- [ ] 已在设计中标明本服务对外提供的调用形态  
- [ ] Agent 节点**不**散落直连他服务私有 HTTP；若需下游，经 MCP Server 内再 HTTP  
- [ ] **禁止** `import` 其他 `services/*` 内部包  

## 2. 配置归属（Settings）

- [ ] 平台共享项：继承/使用 `common.config.settings`（库连接、跨服务 Key 占位等）  
- [ ] 产品开关：本服务 `XxxSettings`（参考 `rag_settings.py` / `ComplaintSettings`），**不要**把产品 `KB_*` 塞回平台 Settings  
- [ ] `.env.example` 分段注释本服务变量  

## 3. 错误码段

- [ ] 使用 step6 为本服务分配的 **A?xxx** 段，禁止私自编号  
- [ ] 对外 API：`code` + 安全 `message`；内部 cause 仅日志  

## 4. 必须使用的 common 能力（按场景勾选）

- [ ] **LLM HTTP**：`common.llm.transport`（`post_chat_completions` / `post_json`）；组合 reliability，禁止第三条裸 httpx/SDK 通道  
- [ ] **Guardrails**：共享 pre/post 钩子（领域规则可服务内注册）  
- [ ] **安全**：脱敏 / 审计 / 敏感词（出入日志与落库前）  
- [ ] **Trace**：`X-Trace-Id` 中间件与出站透传  
- [ ] **健康检查**：至少 `/health`，compose 可探活  

## 5. 仓库与里程碑口径

- [ ] README / 文档写明本服务是「深做」还是「占位」  
- [ ] 未达里程碑前保持 health-only，不假装完整业务 API  

## 参考

- `openspec/specs/01_arch_constraint.md`  
- `openspec/specs/platform/integration-boundaries/spec.md`  
- `requirements_document/step3_架构设计/01_整体技术架构*.md` §5.1 容量模型  

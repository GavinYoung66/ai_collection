# complaint-service 外部依赖对齐索引（批次12 / 模块14）

> TBD ≠ 已确认。本批完成标准是文档存在且列齐全，不是外部团队已交付。
> 本批**不实现真实 MCP Server**，**不切换 stub 网关**，编码侧继续 **Mock**。

## 交付物

| 任务 | 文件 | 说明 |
| --- | --- | --- |
| 14.1 | `mcp-server-tracking.md` | 4 Server × 14 tool 进度跟踪表 |
| 14.1 | `mcp-contract-checklist.md` | 契约对齐清单（入参/出参/异常/信封） |
| 14.2 | `rag-kb-confirmation.md` | rag-service 法规/范本入库与质量门槛 |
| 14.3 | `llm-quota-confirmation.md` | DeepSeek / DashScope 配额、QPM、连通 |
| 14.4 | `bizsys-integration-confirmation.md` | 工单推送、回调、复核界面 |

## status 枚举

`not_started` | `in_progress` | `contract_confirmed` | `ready_for_switch` | `blocked`

`ready_for_switch` 仅表示外部具备切换条件，**不**授权本批打开真 MCP / 真 LLM。

## 本批 14.x 快照（2026-09-06）

| ID | 状态 | 责任方 | 说明 |
| --- | --- | --- | --- |
| 14.1 | 部分对齐 | MCP Server 各域团队（见跟踪表 `owner_team`） | 14 tool 契约已落表；外部 Server 未交付；本仓无 Mock MCP 实现 |
| 14.2 | 阻塞中 | rag-service 团队 | 法规/范本入库与三项质量指标均为 TBD |
| 14.3 | 阻塞中 | 算法/基础设施团队 | 模型与 endpoint 基线已记；配额/单价/连通 TBD；密钥不入库 |
| 14.4 | 阻塞中 | 传统催收业务系统团队 | 推送/回调/复核契约已列出；对端未确认；本仓 complaints API 未实现 |

## 硬约束

- 禁止改 `services/complaint-service` 业务逻辑、图、API、测试语义。
- 真实密钥、配额账号口令不要写入仓库；用占位或「由部署/对接方线下确认」。
- 本仓 DD-05 为 v1.1（有 §4.1–4.3，无独立 §4.4 正文）；14 tool 以本批规划清单为契约基线。

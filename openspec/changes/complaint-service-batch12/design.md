## Context

当前状态：
- 本仓 `origin/dev` 上 complaint-service 仍为 health stub；真实 4 个 MCP Server、rag-service 投诉法规/范本生产入库、DeepSeek/DashScope 配额合同、传统催收业务系统推送/回调/复核 UI 仍由外部团队交付。
- 编码侧规划继续使用 Mock MCP / Mock LLM；prod MCP 网关若编排则为 stub。
- 仓库原先无 `docs/complaint-service/`；PRD 契约基线在 `requirements_document/step2_需求PRD/DD-05-COMPLAINT-JUDGMENT.md`（本仓为 v1.1：有 §4.1–4.3，无独立 §4.4 正文）。本批 14 tool 清单以模块14 规划基线为准（补齐 P1.2 漏列项）。

目标见 proposal.md：把模块 14 做成可 grep 验收的跟踪/确认文档，不写业务代码。

## Goals / Non-Goals

**Goals**
- 钉死交付路径与表头，apply 只写文档。
- 14.1～14.4 字段完备；已知 PRD/代码事实预填，未知值 `TBD`。
- 验收：文件存在 + 表头/关键字符串 grep，不跑 docker / pytest 业务。

**Non-Goals**
- 实现真实 MCP Server，或把 stub 网关换成真 MCP。
- 改研判链路 / 图节点 / API / LLM Provider / compose。
- 打开 pre/prod 真 LLM / 真 MCP 开关。
- 把「外部团队已交付且质量达标」当作本批完成条件（完成 = 文档骨架+列齐全，含 TBD）。
- Skill 架构；K8s。

## Decisions

### D1：文档根目录用 `docs/`，不用 `requirements_document/`

**选择**：全部本批交付物落在：

```
docs/complaint-service/external-deps/
  README.md
  mcp-server-tracking.md
  mcp-contract-checklist.md
  rag-kb-confirmation.md
  llm-quota-confirmation.md
  bizsys-integration-confirmation.md
```

`requirements_document/` 保持 PRD/任务清单只读基线；跟踪表是会随外部进度改的活文档。

**替代**：写进 `requirements_document/step8_...` —— 否决（会污染需求基线版本）。写进 `openspec/specs/` —— 否决（主 spec 不是 PM 跟踪表）。

### D2：进度 `status` 枚举

**选择**：`not_started` | `in_progress` | `contract_confirmed` | `ready_for_switch` | `blocked`。单元格可为枚举或 `TBD`。`ready_for_switch` 只表示外部具备切换条件，**不**授权本批改 Mock 开关或 stub 网关。

### D3：Server 名以下划线为权威，PRD 连字符进 `prd_alias`

| 权威名 | PRD 别名 |
| --- | --- |
| `business_ticket` | business-ticket |
| `call_qc` | call-qc |
| `rag_kb` | rag-kb |
| `data_platform` | data-platform-writeback |

14 tool 以模块14 规划基线为准（含 P1.2 漏列的 `get_complaint_ticket`、`query_call_sessions`、`search_complaint_handling_guidelines`、`push_rag_candidate`）。

### D4：apply 产出「列齐全的确认文档」，允许 TBD

**选择**：apply 预填 14 tool 名、入参/出参/异常、质量门槛数字、三模型名、推送/回调/复核枚举。供应商配额、实测准确率、签字日期等填 `TBD`。密钥/口令 **不得** 入库。本批完成 ≠ 外部已就绪。

### D5：本批禁止 Mock→真实切换

**选择**：文档用固定声明块：编码继续 Mock；prod 网关保持 stub；切换是外部交付后的后续变更。apply MUST NOT 改 `services/complaint-service/`、`config/*.env`、compose、MCP/LLM 代码。

### D6：验收只做文档存在性与列完备性

**选择**：tasks 验证命令形态：`test -f <path>` + `grep -E '...' <path>`。禁止 `docker build`、`pytest`、对真实 MCP/LLM 发流量作为本批门禁。

### D7：14.1 拆成跟踪表 + 契约清单两文件

进度（谁、状态、阻塞）与契约（字段、异常、信封）分离。README 做索引。

### D8：LLM 同时记 QPM 与 QPS

两列都要。本仓 DD-05 v1.1 NFR 写 qwen-turbo 30 QPS、qwen-plus 10 QPS 作对照备注，不是已确认合同。`unit_price_reasoning` 仅 deepseek-r1 有意义；千问写 `N/A`。`reasoning_content` 专节对接 CoT 加密字段名 `cot_encrypted`，不改代码。真实密钥写「由部署/对接方线下确认」。

### D9：3.3/3.4 收口进 14.1，不另开编码任务

模块3 的「与 MCP Server 团队确认契约」由契约清单关闭。不改业务代码。

## Risks / Trade-offs

- **[TBD 被当成已对齐]** → README 写「TBD ≠ 已确认」；`ready_for_switch` 不触发本批切流量。
- **[本仓 DD-05 v1.1 无 §4.4 正文]** → 14 tool 以本批规划清单为基线，并在 README 注明。
- **[外部进度本批结束仍为 blocked]** → 允许；完成标准是文档而非外部绿灯。

## Migration Plan

1. 新建 `docs/complaint-service/external-deps/`。
2. 写 README 索引 + D5 声明块。
3. 按 D1 六文件写入预填表（已知列 + TBD）。
4. grep 验收。
5. 回滚：删除该目录即可。

## Open Questions

- 各外部团队具体负责人姓名（`owner_team` 用角色占位）。
- 供应商真实配额与单价（合同未到则保持 TBD）。
- RAG 评测集存放位置（`dataset` 列 TBD）。

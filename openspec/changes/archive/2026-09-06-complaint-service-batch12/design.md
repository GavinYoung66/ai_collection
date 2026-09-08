## Context

当前状态：
- 批次0～11 已归档：Mock MCP/LLM/推送、MCP Client、LLM 路由、图/API/业务服务、测试 CI、Dockerfile 与 compose（prod MCP 网关为 **stub**）均已落地。
- 批次11 `config/pre.env` / `prod.env` 已写 `USE_MOCK_MCP=false` / `USE_MOCK_LLM=false`（部署契约），但真实 MCP/LLM 仍未由外部交付；**文件上 Mock=false ≠ 外部已就绪**。
- 真实 4 个 MCP Server、rag-service 投诉法规/范本生产入库、DeepSeek/DashScope 配额合同、传统催收业务系统推送/回调/复核 UI 仍由外部团队交付。
- 编码侧 dev/test 使用 `USE_MOCK_MCP` / `USE_MOCK_LLM`；模块 3.3/3.4 的契约确认未单独成文档，收口到本批 14.1。
- 仓库尚无 `docs/complaint-service/`；PRD 契约基线在 `requirements_document/step2_需求PRD/DD-05-COMPLAINT-JUDGMENT.md` §4.4 / 7.1 / 8.2 / 8.5 / 11.2。

目标见 proposal.md：把模块 14 做成可 grep 验收的跟踪/确认文档，不写业务代码。

## Goals / Non-Goals

**Goals**
- 钉死交付路径与表头，apply 只写文档。
- 14.1～14.4 字段完备；已知 PRD/代码事实预填，未知值 `TBD`。
- 验收：文件存在 + **表头行**（`| col |`）/关键字符串 grep，不跑 docker / pytest 业务。
- 完成标准钉死：文档收口即可归档，不要求外部全绿。

**Non-Goals**
- 实现真实 MCP Server，或把批次11 stub 网关换成真 MCP。
- 改研判链路 / 图节点 / API / LLM Provider / `model_router.yaml` / compose / `config/*.env`。
- 把 pre/prod 已有的 `USE_MOCK_*=false`「改回 true」或本批打开真流量。
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

**选择**：`not_started` | `in_progress` | `contract_confirmed` | `ready_for_switch` | `blocked`。单元格可为枚举或 `TBD`。`ready_for_switch` 只表示外部具备切换条件，**不**授权本批改 `USE_MOCK_*` 或 stub 网关。

**替代**：自由文本状态 —— 否决（无法 grep 一致性）。

### D3：Server 名以下划线为权威，PRD 连字符进 `prd_alias`

**选择**：与批次0 Mock `SERVERS`、批次3 `mcp-client` 一致：

| 权威名 | PRD §4.4 别名 |
| --- | --- |
| `business_ticket` | business-ticket |
| `call_qc` | call-qc |
| `rag_kb` | rag-kb |
| `data_platform` | data-platform-writeback |

14 tool 以 PRD §4.4 为准（含 P1.2 漏列的 `get_complaint_ticket`、`query_call_sessions`、`search_complaint_handling_guidelines`、`push_rag_candidate`）。

**替代**：跟踪表改用 PRD 连字符 —— 否决（与代码/Mock 不一致）。

### D4：apply 产出「列齐全的确认文档」，允许 TBD；完成 = 文档非外部绿灯

**选择**：apply 预填：14 tool 名、PRD 入参/出参/异常、质量门槛数字、三模型名、推送/回调/复核枚举与 10 项必显。供应商配额、实测准确率、签字日期等填 `TBD`。

**step8 完成含义（本批）**：六文件落档 + 表头/预填可 grep 通过。跟踪表允许整表仍为 `blocked`/`TBD`。归档说明可保留外部阻塞项，**不得**因外部未签字而判定本批失败。

**替代**：等外部签字再落文档 —— 否决（批次12 无法收口）。空模板无预填 —— 否决（无法对照契约）。

### D5：本批禁止 Mock→真实切换；不改批次11 env

**选择**：README + 各确认文固定声明块须写明：

1. 本批不实现真实 MCP Server；不切换批次11 stub 网关。
2. **不改** `services/complaint-service/config/*.env` 与 compose。
3. 批次11 pre/prod 已写 `USE_MOCK_*=false` 是部署意图声明；**≠** 外部 MCP/LLM 已就绪；切真流量须另开变更（契约确认 + `ready_for_switch` + 联调任务）。
4. 「编码侧继续 Mock」指：dev/test 与本地联调路径、以及在外部未就绪前不得把 stub 当生产联调完成；**不是**要求本批把 pre/prod 的 Mock 改回 `true`。

**替代**：本批顺手改 pre/prod Mock —— 否决。

### D6：验收用表头行 grep，避免正文误命中

**选择**：表头验证形态优先：

```bash
grep -E '^\| *<col>( *\| *<col>)+' <file>
# 或单列：grep -E '^\|[^|]*\| *<col> *\|' <file>
```

禁止仅用 `grep -E 'server|tool|status'` 这种易被正文误命中的宽模式作为唯一验收。tool 名、模型名等唯一标识仍可用 `grep -q` 全文。禁止 `docker build` / `pytest` / 真流量作本批门禁。

### D7：14.1 拆成跟踪表 + 契约清单两文件

**选择**：进度（谁、状态、阻塞）与契约（字段、异常、信封）分离，避免一张表过宽。README 做索引。

### D8：LLM 同时记 QPM 与 QPS

**选择**：两列都要。PRD 8.7 写的是 QPS（deepseek-r1=5，qwen-turbo=20）作对照备注，不是已确认合同。`unit_price_reasoning` 仅 deepseek-r1 有意义；千问写 `N/A`。`reasoning_content` 专节对接已有 CoT 加密字段名，不改代码。

### D9：3.3/3.4 收口进 14.1，不另开编码任务

**选择**：模块3 的「与 MCP Server 团队确认契约」由契约清单关闭。不改 `mcp-client` spec。

### D10：本批变更文件集合

**选择**：本批 apply 允许新增/修改的路径 **仅** `docs/complaint-service/external-deps/**`（及本 change 的 openspec 若需同步勾选）。验收：`git status` / 本批 diff 的文件列表 ⊆ 该前缀；不得出现 `services/complaint-service/`、`docker-compose*.yml`、`.github/` 等因本批产生的修改。

## Risks / Trade-offs

- **[TBD 被当成已对齐]** → README 与每份文档写「TBD ≠ 已确认」；`ready_for_switch` 不触发本批切流量。
- **[pre/prod USE_MOCK=false 被误读为已切真]** → D5 声明块强制写边界。
- **[P1.2 与 §4.4 tool 数不一致]** → 以 §4.4 的 14 个为准，跟踪表列出漏列 4 tool。
- **[PRD 11.6 回调 HMAC 正文弱]** → 业务系统确认表保留 `hmac`/`auth` 列，对接方填 TBD 或 N/A。
- **[外部进度本批结束仍为 blocked]** → 允许；完成标准是文档而非外部绿灯（D4）。
- **[有人 apply 时改业务代码]** → D10 + tasks 14.5.1；验证不含 pytest。

## Migration Plan

1. 新建 `docs/complaint-service/external-deps/`。
2. 写 README 索引 + D5 声明块（含批次11 env 边界）。
3. 按 D1 六文件写入预填表（PRD 已知列 + TBD）。
4. 按 D6 表头行 + 关键串 grep 验收。
5. 回滚：删除该目录即可；不涉及服务部署、不回滚 env。

## Open Questions

- 各 MCP Server / rag-service / 算法基建 / 传统业务系统的具体负责人姓名（文档 `owner_team` 可用角色占位，apply 不阻塞）。
- 供应商真实配额与单价（合同未到则保持 TBD）。
- RAG 评测集存放位置（`dataset` 列 TBD）。

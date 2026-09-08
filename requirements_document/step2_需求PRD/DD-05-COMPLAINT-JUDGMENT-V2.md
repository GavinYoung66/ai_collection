# DD-05 V2：投诉异议智能研判系统（第二版增量需求）

> 文档类型：需求 PRD（增量 / 薄规格）  
> 版本：v1.0  
> 状态：定稿（个人本地项目）  
> 日期：2026-09-07  
> 负责人：Ellis Guo  
> 基线：对齐 `DD-05-COMPLAINT-JUDGMENT.md`（V1 已落地）+ 本地已验证闭环（create → `run_graph=true` → review → close）  
> 约束：本文件只描述 **V2 增量**；不重复 V1 全量架构。V1 仍是运行时骨架（LangGraph + MCP Client + API）。

---

## 1. 背景与目标

### 1.1 背景

V1（M3）已完成服务骨架、多 Agent 图编排、Mock MCP/LLM、API、部署与冒烟。本地可用 curl 跑通主链路，但：

- 外部证据仍是偏「骨架级」Mock，业务感不强；
- LLM 默认 Mock，看不出真实模型研判质量；
- 人审靠 curl，体验差，不适合日常自测。

### 1.2 V2 目标（必须）

1. **假数据增强**：在仍不接真业务 MCP Server 的前提下，准备可复用的种子投诉与更完整的 Mock fixture，支撑真图研判。  
2. **接入真实大模型**：关闭 Mock LLM，使用真实 API（DeepSeek / DashScope 等，以现有 `model_router.yaml` 与配置项为准）。  
3. **人审前端**：提供最小可用 Web 界面，由 Ellis Guo 在浏览器完成查看草稿、复核、关单。

### 1.3 非目标（本版明确不做）

- 不接真实业务工单 / 通话质检 / 数据中台 MCP Server（继续 Mock）。  
- 不接真实 RAG 生产知识库（可用 Mock 法规/模板 fixture 增强）。  
- 不做复杂运营后台、权限体系、批量复核工作台。  
- 不重做 V1 LangGraph 拓扑（除非接真 LLM 后发现阻塞性 bug）。  
- 不要求图内 `interrupt` 与前端完全打通 resume（V2 允许继续走 API `PUT /review` 状态机；若顺手可增强，但不阻塞验收）。

---

## 2. 范围与优先级

| ID | 能力 | 优先级 | 说明 |
|----|------|--------|------|
| REQ-CJ-V2-001 | 种子投诉与 Mock 证据数据 | P0 | 至少 3～5 条可区分场景的假数据 |
| REQ-CJ-V2-002 | 真 LLM 接入与配置 | P0 | `USE_MOCK_LLM=false` + 密钥环境变量 |
| REQ-CJ-V2-003 | 人审最小前端 | P0 | 列表/详情/复核/关单 |
| REQ-CJ-V2-004 | V2 联调验收脚本或清单 | P1 | 一键或逐步验证假数据+真模型+前端 |

依赖顺序：

```text
假数据（001）──→ 真 LLM（002）──→ 人审前端（003）
                      └──────────→ 联调验收（004）
```

---

## 3. 功能需求

### 3.1 假数据（REQ-CJ-V2-001）

**要求**

- 提供种子投诉 JSON/脚本，可 `POST /api/v1/complaints` 批量或单条创建。  
- 增强 Mock MCP fixture（`tests/mock/mcp/fixtures/` 或等价路径），覆盖：
  - 业务工单：`query_case_info` 等关键 tool；
  - 通话质检：ASR / 违规片段（至少 1 条「疑似违规」场景）；
  - RAG：法规与答辩模板非空结果。  
- 场景建议（可增减，但不少于 3 类）：
  1. 坐席话术/态度恶劣（偏 `agent_violation`）；
  2. 用户误解计息/规则（偏 `user_misunderstanding`）；
  3. 流程合规但体验差（偏 `compliant_but_poor_experience`）。  
- 假数据中的 PII 使用明显虚构号码/姓名；禁止使用真实客户数据。

**验收**

- 用种子数据创建工单后，`run_graph=true` 能读到非空证据上下文（日志或 agent-logs / 中间产物可观察）。  
- 不同场景的投诉文案可区分，便于对比真模型输出。

### 3.2 真实大模型（REQ-CJ-V2-002）

**要求**

- 本地/dev 可通过环境变量或 `config/dev.env`（密钥不入库明文提交）启用真模型。  
- `USE_MOCK_LLM=false`；`USE_MOCK_MCP` 保持 `true`。  
- 复用现有 `ComplaintLLMProvider` + `model_router.yaml`；按角色路由到配置的模型。  
- 调用失败时：保留 V1 降级/错误可见性（至少 API 不静默空成功；可转人工或返回明确错误）。  
- 文档写清：需要哪些 Key、哪个 base_url、如何验证连通。

**验收**

- 同一条种子工单，`run_graph=true` 返回的 `verdict` / `response_draft` 不再是固定 Mock 模板句式（或与 Mock 固定输出可区分）。  
- health 仍可用；密钥未出现在 git 跟踪的明文文件中（`.env` / 本地 secret）。

### 3.3 人审前端（REQ-CJ-V2-003）

**要求**

- 最小页面集合：
  1. **列表**：展示投诉 id、摘要、task_status、verdict；  
  2. **详情**：投诉文本、判责、置信度、答辩草稿、agent-logs（可折叠）；  
  3. **复核**：至少支持 `approve`；建议支持 `reject_rejudge` / `escalate`（六动作可分阶段，P0 至少 approve + 一种驳回或升级）；  
  4. **关单**：`resolved` 后可 close。  
- 调用现有后端：`/api/v1/complaints*`（8006）。  
- 实现形态不限：独立静态页 / 轻量 React/Vue / Gradio 均可，优先**改动小、本地好启动**。  
- 仅本地使用即可，不做复杂登录（可后续加）。

**验收**

- 浏览器完成：打开列表 → 点进已研判工单 → approve → close，状态与 API 一致。  
- 不依赖 curl 完成人审主路径。

### 3.4 联调验收（REQ-CJ-V2-004）

**要求**

- 提供简短验收步骤（文档或脚本）：种子创建 → 真图 → 前端人审 → 关单。  
- 失败时有排查提示（服务未起、Key 无效、Mock 数据缺失等）。

---

## 4. 非功能与约束

| 项 | 要求 |
|----|------|
| 端口 | 后端保持 **8006** |
| 编排 | 继续 LangGraph；不改为 Skill 运行时编排 |
| 外部调用 | 证据层继续 MCP Client；V2 仍 Mock Server |
| 安全 | API Key / 密钥不提交仓库；日志继续脱敏约定 |
| 个人项目 | 文档与实现以「跑通、可自测」为准，不做企业多团队流程 |

---

## 5. 验收总标准（V2 Done）

同时满足：

1. 至少 3 条种子场景可一键或文档步骤创建；  
2. `USE_MOCK_LLM=false` 下真图研判成功并进入 `pending_review`；  
3. 前端可完成 approve（及关单）；  
4. `USE_MOCK_MCP=true` 无需真外部系统；  
5. 冒烟脚本（stub 路径）不回归失败。

---

## 6. 与 V1 文档关系

| 文档 | 关系 |
|------|------|
| `DD-05-COMPLAINT-JUDGMENT.md` | V1 全量需求，仍然有效 |
| 本文 `DD-05-COMPLAINT-JUDGMENT-V2.md` | V2 增量；冲突时以「V2 非目标 / 增量条款」为准（本地个人迭代） |
| `05_M3_...清单.md` | V1 任务，已完成参考 |
| `06_M3.5_投诉研判系统V2清单.md` | V2 开发任务清单（实现依据） |

---

## 修订记录

| 版本 | 日期 | 说明 | 作者 |
|------|------|------|------|
| v1.0 | 2026-09-07 | 新建 V2 增量需求：假数据 + 真 LLM + 人审前端 | Ellis Guo |

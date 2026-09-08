## Context

See proposal.md — Why. V2 静态页已在 `services/complaint-service/web/`，后端 8006 已有 create/list/detail/judge/review/close/agent-logs。演示缺口在「一键推送、点选开跑、可读追踪、中文操作」。追踪数据源已有 `agent-logs`、`mcp_call_audit`、`real_llm_invoke` 等分散日志；**必须在 judge 路径落入本工单可查询存储**，再由 `/trace` 聚合，而非只读进程内 call_log。

## Goals / Non-Goals

**Goals:**

- 浏览器完成：模拟推送 → pending 点选研判 → 状态条 + 本地链路时间线 → 通过 → 关单
- 最小 API 增量：`POST .../demo/seed` + `GET .../trace`；前端保持静态单页
- 追踪 Layer1 本地可读（落单 + 查询）；Layer2 LangSmith 外链可选（非主验收）

**Non-Goals:**

- 真 MCP / 真中台 / interrupt resume / 运营级 SPA
- 以 LangSmith UI 替代本地时间线
- 大改 LangGraph 边与节点职责

## Decisions

### D1 — 灌数：后端 demo seed，每次新 id

- **选择**：`POST /api/v1/complaints/demo/seed` 一次创建恰好 3 条；**每次调用生成带唯一后缀（uuid/时间戳）的新 `complaint_id`**；场景仍固定三类（坐席违规 / 用户误解 / 体验差）。Mock fixture 按场景关键字/前缀匹配，**不**依赖写死旧 `case-v2-*` 作为唯一 id。
- **备选**：固定三 id 幂等覆盖 — 第二次推送语义含糊且易与 V2 脚本互踩，拒绝。
- **理由**：演示可反复点推送；路由须挂在 `{id}` 之前防吞路径。
- **与 V2 关系**：`seed_complaint_v2.sh` 仍可独立使用固定 id；demo seed 走新 id 策略，互不强制覆盖。

### D2 — 点选研判：仅 pending 自动 judge

- **选择**：列表 click：`pending` → judge+加载/防连点（列表可显示「研判中」）→ detail；其它状态 → detail only；详情「重新研判」显式按钮。
- **重新研判启用态**：仅 `pending` / `pending_review`（及文档约定失败态）可点；**`resolved` 与 `closed` MUST 禁用**（本变更不允许结案后静默重跑）。
- **备选**：每次 click 都重跑 — 贵、乱、误触，拒绝。

### D3 — 追踪：落单 + `GET /trace`；LangSmith 可选

- **选择（默认路径）**：对外主契约为 **`GET /api/v1/complaints/{id}/trace`**。`agent-logs` 保持兼容只读旧摘要，**不是** V3 时间线主契约。
- **落单（强制）**：`judge(run_graph=true)` 路径 MUST 将 Agent / MCP / LLM / RAG（若有）事件**关联写入该 complaint 可查询存储**（扩展 `agent_logs` 结构或专用 trace 列表均可）；查询层只读该存储做聚合。**禁止**仅依赖进程内 Provider/MCP `call_log` 而未落单。
- **事件字段**：时间、类别（Agent/MCP/LLM/RAG/其他）、名称、耗时或起止、成功/失败摘要；可选 `langsmith_url`。
- **LangSmith**：P1 外链；无 Key/无 run id 则隐藏；失败不影响本地时间线。
- **参考**：rag-service 检索 trace/collector 思路，不强行同库。

### D4 — 状态条：前端映射表

纯前端映射，不新增后端状态机表：

| 大致阶段（状态条） | 主要依据（示例） |
|--------------------|------------------|
| 接单 | 已创建 / `pending` |
| 取证 | `evidence_gathering` 或 trace 含 evidence_* |
| 反思 | trace 含 reflection |
| 三路研判 | `judging` 或 fact/compliance/user_demand |
| 判责 | trace 含 verdict |
| 答辩 | trace 含 generate_package |
| 待人审 | `pending_review` / `pending_senior_review` |
| 已通过 | `resolved` |
| 已关单 | `closed` |

实现时以 `task_status` 为主、trace 最近 Agent 节点为辅；映射表须可被 `v3-demo.md` 引用。

### D5 — 中文按钮：文案映射现有 API

| UI | API |
|----|-----|
| 通过 | `review_action=approve` |
| 驳回重判 | `reject_rejudge` + reason |
| 关单 | `PUT .../close` |
| 重新研判 | `POST .../judge` + `run_graph=true` |

不暴露 escalate/modify_*（可后续加）。

### D6 — 实现分段

A 推送+中文/禁用 → B 点选研判+重新研判 → C 落单埋点 + `/trace` + 时间线 + 状态条（+可选 LangSmith）→ D 文档与 smoke。

## Risks / Trade-offs

- [真 LLM 耗时长] → 列表加载态 + 「研判中」防连点 + toast；文档写明超时预期
- [日志不全导致时间线空洞] → **P0：judge 路径强制落单** Agent/MCP/LLM/RAG span
- [内存存储重启丢单] → 演示文档注明；不在本变更做持久化升级
- [LangSmith 不稳定] → 外链失败不影响本地时间线；**主验收不含外链必过**
- [路径冲突 `/demo/seed`] → 路由注册顺序测试（对标 stats）
- [重复 seed / 与 V2 固定 id 互踩] → demo seed **唯一 id**；V2 脚本仍可独立存在

## Migration Plan

1. 部署/本地 recreate 后热更 `web/` 与 API
2. 无数据迁移；旧工单仍可打开
3. 回滚：回退 web 与新增路由即可，不影响 V2 stub/真图主逻辑

## Open Questions

- LangSmith 项目/组织 URL 模板以本机 `.env` 为准，实现时写进 `v3-demo.md`（不阻塞 specs）
- ~~/trace vs agent-logs~~ → **已拍板：默认 `/trace`**

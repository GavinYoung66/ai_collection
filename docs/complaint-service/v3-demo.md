# Complaint Service V3 演示指南

> 演示向静态前端：模拟中台推送 → 点选研判 → 状态条 + 本地链路追踪。  
> **人审处置请改用 V4**：定责 → 回电 → 关单，见 [`v4-demo.md`](./v4-demo.md)。  
> 端口：后端 **8006**，前端 **8080**。负责人：Ellis Guo。  
> **主路径五项不要求 LangSmith 外链**（P1 可选）。

相关：[`v4-demo.md`](./v4-demo.md)（**当前人审主路径**）、[`v2-acceptance.md`](./v2-acceptance.md)、[`real-llm.md`](./real-llm.md)、[`flow.md`](./flow.md)。

## 0. 起服

```bash
cd /Users/ellisguo/myproject/my_work/ai_collection
# conda activate ai_collection

# 方式 A：compose（推荐日常）
docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service

# 方式 B：本机 uvicorn（改代码后需重启才加载）
# PYTHONPATH=. uvicorn app.main:app --app-dir services/complaint-service --host 0.0.0.0 --port 8006 --reload

curl -s http://localhost:8006/health
```

期望：`"status":"ok"`。

前端：

```bash
cd services/complaint-service/web
python3 -m http.server 8080
```

浏览器打开 <http://localhost:8080>，页眉 `API_BASE` = `http://localhost:8006`。

### 开关（演示默认）

| 变量 | 演示建议 | 说明 |
|------|----------|------|
| `USE_MOCK_MCP` | `true` | 证据/MCP 走 Mock，不接真中台 |
| `USE_MOCK_LLM` | `true`（快）或 `false`（真模型） | `false` 时见 [`real-llm.md`](./real-llm.md)；真图可能数十秒 |
| `LANGSMITH_ENABLED` / `LANGSMITH_API_KEY` | 可关 | **非主验收必过**；无 Key 时详情仍显示本地时间线 |
| `LANGSMITH_TRACE_URL_TEMPLATE` | 可选 | 有 Key + run id **且**配置了模板才显示「在 LangSmith 打开」；占位 `{run_id}`/`{project}`。未配置则隐藏，避免错误 404 |

密钥只放本机 `.env` / compose env，**不要提交 git**。

**数据寿命：** 演示工单与 `trace_events` 存于 **complaint-service 进程内存**。重启 / recreate 容器后列表与追踪清空，需重新点「模拟数据中台推送」。前端页眉有橙色提示。

## 1. 模拟数据中台推送（验收①）

1. 点顶部 **「模拟数据中台推送（3 条）」**  
2. Toast 提示 3 个新 `complaint_id`；列表刷新出现 3 条，状态多为 `pending`  
3. （可选）再点一次：应再出现 3 条**不同** id（共 6 条新单）

等价 API：

```bash
curl -s -X POST http://localhost:8006/api/v1/complaints/demo/seed | python3 -m json.tool
# 连续两次 id 集合应不相交；不得返回 A5001
```

## 2. 点选 pending 触发研判（验收②）

1. 在列表点击一条 **`pending`** 行  
2. 出现「研判中…」加载态（防连点）；真 LLM 时可能较久  
3. 完成后自动打开详情，状态期望 **`pending_review`**  
4. 再点已是 `pending_review` / `closed` 的行：**只开详情，不自动 judge**  
5. 详情 **「重新研判」**：仅 `pending` / `pending_review` 可点；`resolved` / `closed` 禁用

等价 API：

```bash
CID=<pending_id>
curl -s -X POST "http://localhost:8006/api/v1/complaints/${CID}/judge" \
  -H 'Content-Type: application/json' \
  -d '{"run_graph":true}' | python3 -m json.tool
```

## 3. 状态条 + 本地链路追踪（验收③）

打开已研判详情：

- **状态条**（页顶）：按下方 D4 映射；`pending_review` 时高亮 **「待人审」**  
- **链路追踪**（右侧）：本地时间线，至少可见 **Agent** 与 **LLM**（有则亦有 MCP/RAG）  
- 无 LangSmith Key：**不报错、不空白阻断**；可提示「无外部 Trace 链接」  
- 「在 LangSmith 打开」：仅当环境启用且本次有外部 run id 时显示（**非五项必过**）

等价 API：

```bash
curl -s "http://localhost:8006/api/v1/complaints/${CID}/trace" | python3 -m json.tool
# 不存在的 id → code A5001
```

### 状态条映射（design D4）

纯前端映射，以 `task_status` 为主、trace 中 Agent 节点名为辅：

| 大致阶段（状态条） | 主要依据（示例） |
|--------------------|------------------|
| 接单 | 已创建 / `pending` |
| 取证 | `evidence_gathering` 或 trace 含 `evidence_*` |
| 反思 | trace 含 `reflection` |
| 三路研判 | `judging` 或 fact / compliance / user_demand |
| 判责 | trace 含 `verdict` |
| 答辩 | trace 含 `generate_package` |
| 待人审 | `pending_review` / `pending_senior_review` |
| 已通过 | `resolved` |
| 已关单 | `closed` |

## 4. 通过 → 关单（验收④）+ 中文按钮（验收⑤）

详情操作区按钮为中文：

| UI | API |
|----|-----|
| 通过 | `PUT .../review`，`review_action=approve` |
| 驳回重判 | `reject_rejudge` + `reason`（前端默认 `wrong_verdict`） |
| 重新研判 | `POST .../judge`，`run_graph=true` |
| 关单 | `PUT .../close` |

步骤：

1. 在 `pending_review` 点 **通过** → 状态 `resolved`；此时可点 **关单**  
2. 点 **关单** → 状态 `closed`；通过 / 驳回 / 关单 / 重新研判均应禁用  

## 5. 冒烟回归（V2 路径不回归）

```bash
PORT=8006 bash scripts/smoke/smoke_complaint_service.sh
```

期望：全部 `[PASS]`（默认 stub judge，不强制真图）。

单测（demo seed + `/trace`）：

```bash
cd /Users/ellisguo/myproject/my_work/ai_collection/services/complaint-service
PYTHONPATH=../..:.. conda run -n ai_collection pytest \
  tests/unit/test_api_routes.py -q \
  -k 'demo_seed or trace'
```

## 常见失败排查

| 现象 | 排查 |
|------|------|
| health / Failed to fetch | 8006 是否在听；CORS；前端是否用 localhost:8080 |
| 推送返回 A5001 | 服务未含 demo seed 路由或未重启；确认 `POST .../demo/seed` 挂在 `{id}` 之前 |
| 点 pending 无「研判中」/ 不进详情 | 硬刷新前端；Network 是否 `POST .../judge` 且 `run_graph:true` |
| 研判很慢 / toast 失败 | 真 LLM 超时或 Key；可先 `USE_MOCK_LLM=true` 验证链路 |
| 链路追踪为空 | 必须对**该单**跑过 `run_graph=true` 且服务进程未丢内存缓存（重启会清空内存工单） |
| 时间线 MCP/LLM 全挤在末尾 | 需服务含审计 `started_at` 落盘；重启后端加载新代码后再研判一单 |
| LangSmith 按钮不见 | 正常（非必过）；需 `LANGSMITH_ENABLED` + Key + `langsmith_run_id` + **`LANGSMITH_TRACE_URL_TEMPLATE`** |
| LangSmith 打开 404 | 检查模板是否含正确 org/project；勿使用未校准的默认 `/runs/{id}` |
| 状态条停在接单 | 详情状态仍是 `pending`；先完成研判 |
| 无法关单 | **V4**：须定责 + 回电到 `resolved` 后关单（见 [`v4-demo.md`](./v4-demo.md)）；旧「通过」按钮已移除 |

## 非目标（本演示不做）

真 MCP / 真数据中台、interrupt↔UI resume、SPA、Skill 运行时、以 LangSmith UI 替代本地时间线。

---

## 验收记录（V3 推送/研判/追踪；人审以 V4 为准）

> 不含 LangSmith 必过。推送→研判→追踪仍按本节；**定责/回电/关单**见 [`v4-demo.md`](./v4-demo.md) 五项。

### 浏览器清单（操作者勾选）

在 <http://localhost:8080>、API=`http://localhost:8006` 下操作：

| # | 浏览器步骤 | 勾选 |
|---|------------|------|
| ① | 点「模拟数据中台推送（3 条）」→ 列表出现 3 条新 id（含一条已 `pending_review`+AI pending）；再点一次共 ≥6 个不同 id | ☐ |
| ② | 点一条状态为 `pending` 的行 → 见「研判中」→ 详情为 `pending_review`；再点非 pending 行 Network **无**新的 `/judge` | ☐ |
| ③ | 详情顶状态条高亮「待人审」；右侧「链路追踪」可见 Agent + LLM（无 LangSmith Key/模板也不报错） | ☐ |
| ④ | **（已迁 V4）** 定责 → 确认回电 → 关单，见 v4-demo | ☐ |
| ⑤ | 中文按钮；`closed` 后定责/回电/关单/退回 AI 均不可点 | ☐ |

### 自动化等价核验（2026-09-07，Agent）

| 项 | 结果 | 备注 |
|----|------|------|
| API ①～③ | **PASS** | `demo/seed` → `judge(run_graph=true)` → `/trace` |
| 人审 ④⑤ | **见 V4** | [`v4-demo.md`](./v4-demo.md) |
| smoke | **PASS** | 13 passed, 0 failed |

P1 补强（2026-09-07）：验收改为「浏览器清单 + 自动化等价」分离；追踪时间戳按调用 `started_at` 排序；LangSmith 外链须配置 `LANGSMITH_TRACE_URL_TEMPLATE`；页眉提示内存重启清空。

**V4 交叉验证后（2026-09-07）：** 本文不再以「通过→关单」为人审主路径；请以 v4-demo 为准。

验收日期：2026-09-07（自动化）；浏览器清单请操作者当场勾选。

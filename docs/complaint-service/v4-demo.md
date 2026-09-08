# Complaint Service V4 演示指南

> 分步人审：定责 → 回电处置 → 关单；成立性显式确认；凡投诉必回电；违规发质检事件。  
> 端口：后端 **8006**，前端 **8080**。负责人：Ellis Guo。  
> 推送 / 研判 / 链路追踪可沿用 [`v3-demo.md`](./v3-demo.md)；本文聚焦 **V4 人审处置闭环**。

相关：[`v3-demo.md`](./v3-demo.md)、[`v2-acceptance.md`](./v2-acceptance.md)、[`flow.md`](./flow.md)。

## 0. 起服

与 V3 相同：

```bash
cd /Users/ellisguo/myproject/my_work/ai_collection
# conda activate ai_collection

docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres redis complaint-service
# 或本机：PYTHONPATH=. uvicorn app.main:app --app-dir services/complaint-service --host 0.0.0.0 --port 8006 --reload

curl -s http://localhost:8006/health
cd services/complaint-service/web && python3 -m http.server 8080
```

浏览器 <http://localhost:8080>，`API_BASE`=`http://localhost:8006`。

| 变量 | 演示建议 |
|------|----------|
| `USE_MOCK_MCP` | `true` |
| `USE_MOCK_LLM` | `true`（快）或 `false`（真模型） |

密钥只放本机 `.env`，**不要提交 git**。工单与事件存**进程内存**，重启需重新「模拟推送」。

## 1. 状态机（design D1）

```
pending → …研判… → pending_review
                 →（定责 disposition/verdict）→ pending_callback
                 →（回电 disposition/callback）→ resolved
                 →（关单 close）→ closed
```

| 状态 | 含义 | 主操作 |
|------|------|--------|
| `pending_review` | 待人审定责 | 三选一确认定责；或退回 AI 重跑 |
| `pending_callback` | 已定责待回电 | 确认回电安排 |
| `resolved` | 已安排回电 | 关单 |
| `closed` | 已关单 | 全部禁用 |

非法跳步（如 `pending_callback` → `closed`）被状态机拒绝。

## 2. 事件契约（mock 可观测）

| 事件 | 何时 | 要点 |
|------|------|------|
| `callback.requested` | 回电处置成功 | 凡投诉必发；含 `complaint_id`、成立结论、`callback_required`、note、operator、`trace_id` |
| `qc.violation.reported` | 坐席违规 **且** 回电成功 | 含 `deduction_reason`、投诉摘要、有序 `evidence`（可空数组） |
| `case.risk.upgraded` | 违规回电时可兼发 | 兼容旧链路；扣分类以 `qc.violation.reported` 为准 |

**幂等：** 同一工单再次 `PUT .../disposition/callback` → 成功已安排，**不重复**发 `callback.requested` / 质检事件。

观测：

```bash
curl -s 'http://localhost:8006/api/v1/complaints/events?topic=callback.requested' | python3 -m json.tool
curl -s 'http://localhost:8006/api/v1/complaints/events?topic=qc.violation.reported' | python3 -m json.tool
```

## 3. 浏览器主路径（分步）

前置：点「模拟推送（3 条）」→ 含 **1 条已 `pending_review` 且 AI=`pending`**（直接打开即可验⑤）；另 2 条多为 `pending`，点选研判到 `pending_review` 后走定责。

### 3.1 定责（成立性）

1. 详情见 AI 初选预填；三选一：坐席违规 / 用户误解 / 体验差  
2. 与 AI 不一致时 **必须** 填改判原因  
3. 「确认定责」→ 状态 `pending_callback`  
4. 文案为「退回 AI 重跑」（不是驳用户）；无模糊「通过」主按钮  
5. 若定责为坐席违规：右侧出现**有序证据清单**（无证据时显式空态）；**无处罚档位**

```bash
curl -s -X PUT "http://localhost:8006/api/v1/complaints/${CID}/disposition/verdict" \
  -H 'Content-Type: application/json' \
  -d '{"establishment":"agent_violation","reviewer":"ops"}' | python3 -m json.tool
```

### 3.2 回电处置

1. 「确认回电安排」→ `resolved`，发布 `callback.requested`  
2. 若坐席违规：另见 `qc.violation.reported`  
3. 未回电时「关单」禁用 / API 失败

```bash
curl -s -X PUT "http://localhost:8006/api/v1/complaints/${CID}/disposition/callback" \
  -H 'Content-Type: application/json' \
  -d '{"reviewer":"ops","note":"请今日回电"}' | python3 -m json.tool
# 再调一次应仍成功且事件条数不增
```

### 3.3 关单

「关单」→ `closed`；全部操作禁用。

```bash
curl -s -X PUT "http://localhost:8006/api/v1/complaints/${CID}/close" \
  -H 'Content-Type: application/json' -d '{"reason":"done"}' | python3 -m json.tool
```

### 3.4 AI = pending

1. 详情黄底横幅：引导「重新研判 / 退回 AI 重跑」+ 补证（**录音 / 电话 / 短信**）  
2. 「重新研判」「退回 AI 重跑」高亮可点  
3. 关单不可用；不得以 pending 结论定责（须改判三类并填原因，或再跑）

## 4. 五项验收清单

> 真外呼 / 真质检执行 **非必过**。主路径以浏览器为准；curl / pytest 为等价。

### 浏览器清单（操作者勾选）

| # | 步骤 | 勾选 |
|---|------|------|
| ① | 显式确认或改判成立性（改判须原因）；去掉模糊「通过」 | ☐ |
| ② | 定责后未回电不可关单（`pending_callback` 关单失败/禁用） | ☐ |
| ③ | 回电后可观测 `callback.requested`；重复调用幂等 | ☐ |
| ④ | 坐席违规可见有序证据（可空态）；回电后有 `qc.violation.reported`；无处罚档位 | ☐ |
| ⑤ | AI=`pending` 横幅引导再跑+补证；无成立结论不可关单 | ☐ |

### 自动化等价核验（2026-09-07，Agent）

| 项 | 结果 | 备注 |
|----|------|------|
| ① 定责 API | **PASS** | `test_disposition_verdict_*` |
| ②③ 回电幂等 + 关单前置 | **PASS** | `test_disposition_callback_*` / `test_close_blocked_until_callback` |
| ④ 证据 + qc | **PASS** | `test_ordered_evidence_*` / `test_qc_event_*` |
| ⑤ pending 拒定责/关单 | **PASS** | pending 结论 A5002；关单仅 resolved |
| smoke | **PASS** | 2026-09-07：13 passed, 0 failed（`PORT=8006 bash scripts/smoke/smoke_complaint_service.sh`） |
| 单测 API（定责/回电/qc） | **PASS** | `tests/unit/test_api_routes.py` 28 passed |

```bash
# 关键 API 单测
cd services/complaint-service
PYTHONPATH=../..:. pytest tests/unit/test_api_routes.py -q

# smoke（需 8006 已起）
PORT=8006 bash scripts/smoke/smoke_complaint_service.sh
```

## 5. 非目标

真 MCP / 真外呼平台 / 真质检扣分执行 / interrupt↔UI resume / 登录 SPA / LangSmith 唯一真相源。

## 6. 常见失败

| 现象 | 排查 |
|------|------|
| 定责失败 A5002 | 是否仍选 pending；改判是否缺原因；状态是否 `pending_review` |
| 关单失败 | 是否未回电仍为 `pending_callback`；须先回电到 `resolved` |
| 无证据清单 | 须先定责为坐席违规；stub 研判有 mock 证据，真图依赖 `trace_events` |
| 无 qc 事件 | 仅坐席违规 + 回电成功才发；查 `/complaints/events?topic=qc.violation.reported` |
| 列表/追踪清空 | 后端重启清空内存；重新推送并研判 |

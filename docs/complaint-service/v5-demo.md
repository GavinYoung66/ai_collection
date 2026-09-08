# Complaint Service V5 Demo 口径（架构统一里程碑）

> 本页锁定 **V5 演示/验收话术**：完整研判必须跑真图 + 人工复核断点。  
> 推送 / 人审处置细节仍见 [`v3-demo.md`](./v3-demo.md)、[`v4-demo.md`](./v4-demo.md)。  
> 架构统一任务见 OpenSpec change `platform-arch-unification-v5`。

## 硬性要求（COMP-04）

| 要求 | 说明 |
|------|------|
| **必须** `run_graph=true` | `POST /api/v1/complaints/{id}/judge` 请求体含 `"run_graph": true`，走 LangGraph，而非默认 stub judge |
| **必须** 经过 `human_review` | 图在人工复核节点 `interrupt`；定责/回电/关单按 V4 分步处置，**不得**把 stub 一键结果说成完整定责 |
| Mock 可默认 | `USE_MOCK_MCP=true`（及按需 Mock LLM）仅影响证据/模型来源，**不**免除人审门禁 |
| evidence_refs（stretch） | `GET …/verdict` 与详情含 `evidence_refs`（doc_id 列表）。**COMP-03 非原 A–G 必选**；**≠ Batch-D 完成** |

### 最小命令示例

```bash
# 1) health
curl -s http://localhost:8006/health

# 2) 接单后研判（必须 run_graph）
curl -s -X POST "http://localhost:8006/api/v1/complaints/${CID}/judge" \
  -H 'Content-Type: application/json' \
  -d '{"run_graph":true}'
```

浏览器 Demo：点 `pending` 行触发研判时，Network 中应看到 `run_graph:true`；状态进入 `pending_review` 后再做人审（见 v4-demo）。

## 禁止表述

- 「默认 judge 即完整 AI 定责闭环」
- 「七系统均已生产完备」（当前为 2 深切片 + 5 占位）

## 相关文档

- [`v3-demo.md`](./v3-demo.md) — 推送 / 真图 / 链路追踪  
- [`v4-demo.md`](./v4-demo.md) — 定责 → 回电 → 关单  
- [`external-deps/README.md`](./external-deps/README.md) — Mock vs Real 依赖  

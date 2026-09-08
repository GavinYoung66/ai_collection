# Design: complaint-service-batch7

## Context

批次6 已交付完整 LangGraph 编排。本批交付模块7（API）与模块10（安全护栏）。`common.security.*` 与 `common.errors.*`（A5001～A5013）已就绪；MCP client 已有 `mask_text` 二次脱敏，本批补告警+审计，不整段重写。

## Goals / Non-Goals

**Goals:**
- 12 路由 Mock 可调通（TestClient）；统一响应包；错误码 A5001～A5013 + A5014（模式切换）
- 脱敏 / RBAC 审计 / L4 后置校验可单测
- 路由挂载无 `/{id}` 吞掉 `stats`/`mode`

**Non-Goals:**
- 批次8 完整业务服务；批次9 灰度矩阵/限流全量
- 真实 Pub-Sub / 真实 PG 落库
- judge 默认跑全图 interrupt（本批 stub）

## Decisions

### D1: 统一响应包与 code 类型

```json
{"code": 0, "message": "ok", "data": {}, "trace_id": "..."}
```

- **成功**：`code` 为 **int `0`**
- **业务/错误**：`code` 为 **string**（如 `"A5014"`），与 `common.errors.codes.ErrorCode` 一致
- Schema：`code: int | str`（Pydantic Union）；文档写清，禁止实现时随意改成全 int
- `trace_id`：来自 `X-Trace-Id` 或生成

### D2: 薄 service stub + judge 默认不跑全图

- `app/service/complaint_service.py`：内存 dict；CRUD/status/verdict/review/close
- **`judge(complaint_id)` 默认路径**：写入 Mock `verdict_draft` + `task_status`（如 draft_generated / pending_review），**不**调用 `build_complaint_graph().ainvoke`（避免 human_review interrupt 挂死 TestClient）
- 可选：`JUDGE_RUN_GRAPH=1` 或测试专用钩子才跑图（带 MemorySaver + 自动 resume），**不作为 7.1 冒烟默认**

### D3: 路由挂载顺序（防冲突）

`main.py` include 顺序必须为：

1. `stats.router`（`/complaints/stats`、`/complaints/consistency`）
2. `mode.router`（`/complaints/mode`）
3. `complaints.router`（含 `/{id}` 与 close/judge/…）

或在同一 `APIRouter(prefix="/complaints")` 内**先注册固定路径，再注册 `/{id}`**。  
`test_api_routes` 必须断言 `GET /api/v1/complaints/stats` 返回统计而非「工单不存在」。

### D4: 错误码 — A5013 保留回流；模式失败用 A5014

| 码 | 含义（权威） |
|---|---|
| A5001～A5013 | 沿用 `common.errors.codes`（A5013=判责样本回流失败） |
| **A5014** | **本批增补：模式切换失败/回滚**（step8 7.3 写 A5013 与 common 冲突 → **以 common 为准，模式改 A5014**） |

本批 tasks：更新 `common/errors/codes.py` + 消息表增加 A5014。

### D5: 脱敏两层

1. **10.1**：在现有 `mask_text` 返回路径上补「命中检测→告警+`audit.log_action`」；禁止推倒重写 MCPClient
2. **10.2**：`mask_before_persist(text)`；挂 provider / 节点日志关键点；不假装已落 PG

### D6: RBAC（10.3）

同前：运营终审可看明文；研发/运维禁生产明文；admin 双人授权解密 CoT；每次明文访问 `log_action`。

### D7: L4 挂钩点（10.4）

`postcheck(...)`：
- 图内：`generate_package` 产出后（可调用）
- API：`GET /{id}/verdict`、（若返回研判结果的）judge 响应前兜底
- 失败重试≤3 → RuleBased 兜底

### D8: PRD 12.2 红线 7 条 — 本批范围（禁止静默砍而未标注）

| 红线 | 本批 |
|---|---|
| 1 禁止暴力/威胁 | **实现**：AC 扫描答辩初稿 |
| 2 禁止伪造身份 | **实现**：AC（法院/公安/律师等词表） |
| 3 停止致电标记 | **挂钩/字段预留**：校验通过后可写标记位 stub；完整联动批次8+ |
| 4 禁止编造政策 | **实现**：答辩与 RAG 比对，未命中→「以坐席核实为准」 |
| 5 禁止自由解读法律 | **部分**：强制 regulation_refs ⊆ RAG doc_id；自由解读全文检测留给增强 |
| 6 双校验 | **部分**：本批落实后置 L4；前置已有 Prompt/结构，不重复造 L1～L3 |
| 7 禁止编造数据 | **实现**：金额/天数须能在证据/RAG 中命中，否则拦截替换 |

### D9: Redis / 事件 stub

- FakeRedis：`csmp:mode:active`、`csmp:mode:gray_percent`；失败回滚 + **A5014**
- `EventPublisher.publish("complaint.closed", ...)` 仅可断言，不接 MQ

## Risks / Trade-offs

- **[PYTHONPATH]** → `../..` 才能 import `common.*`
- **[step8 A5013 vs common]** → 已用 A5014 化解，归档说明即可
- **[judge stub]** → 批次8 再接真图+resume

## Migration Plan

1. 增补 A5014；统一响应工具；薄 stub service
2. stats/mode 路由 → complaints 路由（挂载顺序 D3）
3. 7.5 main 挂载 + handlers
4. 10.1 补审计 → 10.2 钩子 → 10.3 → 10.4
5. TestClient 12 路由 + security 单测

## Open Questions

- （已关闭）模式失败错误码 → **A5014**
- （已关闭）judge 是否默认真跑图 → **否，stub**
- （已关闭）stats 与 {id} 冲突 → **挂载顺序 D3**
